"""Sampled, own-process-group memory control shared by queued experiments."""
import os
from pathlib import Path
import signal
import re
import subprocess
import time

GIB = 1 << 30


class ResourceRetry(RuntimeError):
    pass


def resource_failure(code, tail):
    return code in (-9, 137) or bool(re.search(
        r'MemoryError|std::bad_alloc|Cannot allocate memory|[Oo]ut of memory|Signals\.SIGKILL|exit status 137\b', tail))


def available():
    return int(next(s.split()[1] for s in Path('/proc/meminfo').read_text().splitlines()
                    if s.startswith('MemAvailable:'))) * 1024


def read_pss(text):
    return sum(int(s.split()[1]) * 1024 for s in text.splitlines() if s.startswith('Pss:'))


def group_memory(group):
    rss = pss = count = 0
    pss_complete = True
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            words = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
            if int(words[2]) != group:
                continue
            count += 1
            rss += int((entry / 'statm').read_text().split()[1]) * os.sysconf('SC_PAGE_SIZE')
            try:
                pss += read_pss((entry / 'smaps_rollup').read_text())
            except FileNotFoundError:
                if entry.exists():
                    pss += read_pss((entry / 'smaps').read_text())
            except PermissionError:
                pss_complete = False
        except (FileNotFoundError, ProcessLookupError):
            pass
    return dict(rss_bytes=rss, pss_bytes=pss if pss_complete else None,
                pss_complete=pss_complete, process_count=count)


def pressure_action(free, stopped, pss):
    if pss is not None and pss > 32 * GIB:
        return 'retry'
    if not stopped and free < 8 * GIB:
        return 'pause'
    if stopped and free > 12 * GIB:
        return 'resume'
    return 'wait'


def terminate(child):
    # Child was created in a new session and its PID is the owned group ID.
    for sig in (signal.SIGCONT, signal.SIGTERM):
        try:
            os.killpg(child.pid, sig)
        except ProcessLookupError:
            pass
    try:
        child.wait(timeout=20)
    except subprocess.TimeoutExpired:
        os.killpg(child.pid, signal.SIGKILL)
        child.wait()


def execute(command, cwd, logfile, update, env=None):
    with Path(logfile).open('w') as log:
        child = subprocess.Popen(command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                                 env=dict(os.environ, **(env or {})), start_new_session=True)
        stopped = False
        peak_rss = peak_pss = 0
        try:
            while child.poll() is None:
                memory = group_memory(child.pid)
                free = available()
                peak_rss = max(peak_rss, memory['rss_bytes'])
                peak_pss = max(peak_pss, memory['pss_bytes'] or 0)
                action = pressure_action(free, stopped, memory['pss_bytes'])
                if action == 'retry':
                    terminate(child)
                    raise ResourceRetry('Own proportional memory exceeded32GiB; retry after headroom returns')
                if action in ('pause', 'resume'):
                    os.killpg(child.pid, signal.SIGSTOP if action == 'pause' else signal.SIGCONT)
                    stopped = action == 'pause'
                update(dict(**memory, own_process_group=child.pid, available_bytes=free,
                            paused=stopped, peak_summed_rss_bytes=peak_rss,
                            peak_pss_bytes=peak_pss, sampled_only=True))
                time.sleep(3)
        except BaseException:
            if child.poll() is None:
                terminate(child)
            raise
        if child.returncode:
            # SIGKILL can indicate OOM but is not itself proof of it. Retrying
            # after headroom is reasonable; ordinary tool errors stay blocked.
            with Path(logfile).open('rb') as stream:
                stream.seek(max(0, Path(logfile).stat().st_size - (1 << 16)))
                tail = stream.read().decode(errors='replace')
            if resource_failure(child.returncode, tail):
                raise ResourceRetry('Stage killed; retry after resource headroom, preserving failed evidence')
            raise RuntimeError('Stage failed with exit code ' + str(child.returncode))
