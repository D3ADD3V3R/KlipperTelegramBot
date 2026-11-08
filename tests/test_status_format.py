import unittest
from klipper_telegram.status_format import status_message


class StatusFormatTests(unittest.TestCase):
    def test_printing(self):
        result = status_message('Ender <3', {
            'state': 'printing', 'filename': 'cube<&>.gcode',
            'print_duration': 5040, 'info': {'current_layer': 156, 'total_layer': 220}},
            {'progress': 0.71}, estimated_time=10000)
        self.assertIn('cube&lt;&amp;&gt;.gcode', result)
        self.assertIn('1 h 24 min', result)
        self.assertIn('48 min 20 s', result)
        self.assertIn('156 / 220', result)
        self.assertIn('71,0 %', result)
        self.assertIn('<b>Dateiname:</b>', result)

    def test_completed(self):
        result = status_message('Ender', {'state': 'complete', 'filename': 'cube.gcode',
            'total_duration': 7560, 'print_duration': 7200, 'filament_used': 12450})
        self.assertIn('2 h 06 min', result)
        self.assertIn('12,45 m', result)

    def test_missing_fields(self):
        result = status_message('Ender', {'state': 'printing', 'filename': ''})
        self.assertIn('Layer:</b> –', result)
        self.assertIn('Schätzung):</b> –', result)

    def test_other_states_unchanged(self):
        self.assertEqual(status_message('Ender', {'state': 'paused', 'filename': 'cube.gcode'}),
                         'Ender: paused\ncube.gcode')

    def test_invalid_numbers(self):
        result = status_message('Ender', {'state': 'complete', 'filament_used': float('nan')})
        self.assertIn('Filamentverbrauch:</b> –', result)

if __name__ == '__main__':
    unittest.main()
