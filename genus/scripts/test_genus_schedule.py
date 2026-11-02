import threading
import unittest
from genus_schedule import schedule


class ScheduleTests(unittest.TestCase):
    def test_two_leaves_overlap_parents_wait_and_large_units_run_alone(self):
        barrier=threading.Barrier(2);lock=threading.Lock();active=set();peak=0
        units=[dict(unit='A',children=[]),dict(unit='B',children=[]),dict(unit='Large',children=['A']),dict(unit='Top',children=['B','Large'])]
        def execute(unit,done):
            nonlocal peak
            name=unit['unit']
            self.assertTrue(set(unit['children'])<=done.keys())
            with lock:
                active.add(name);peak=max(peak,len(active))
                if name=='Large':self.assertEqual(active,{'Large'})
            if name in ('A','B'):barrier.wait(timeout=2)
            with lock:active.remove(name)
            return name
        result=schedule(units,execute,2,{'Large'})
        self.assertEqual(peak,2);self.assertEqual(set(result),{'A','B','Large','Top'})

    def test_bad_dependencies_fail(self):
        for units in ([dict(unit='A',children=['missing'])],[dict(unit='A',children=['B']),dict(unit='B',children=['A'])]):
            with self.assertRaises(ValueError):schedule(units,lambda u,d:None,2)
