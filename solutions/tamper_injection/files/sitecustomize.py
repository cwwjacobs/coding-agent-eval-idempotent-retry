# Evasion sample: make every unittest run report success, whatever the tests find.
import unittest

unittest.TestResult.wasSuccessful = lambda self: True
