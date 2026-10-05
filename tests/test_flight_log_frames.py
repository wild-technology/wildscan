"""Flight-log coordinate system: every trajectory is imported in WGS84 UTM,
in the zone named by the log's own filename tag. Covers the committed
template, UTM generation via write_flight_log_params, and require_utm_zone
(the guard the align, merge and grow stages run before every import).
Offline - no RealityScan interaction."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

from modules.flight_logs import (params_template_frame, require_utm_zone,
                                 write_flight_log_params)

REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
METADATA = os.path.join(REPO, 'modules', 'realityscan_interface',
                        'RS_CLI', 'Metadata')
UTM_TEMPLATE = os.path.join(METADATA, 'FlightLogParams.xml')


def read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


class TestCommittedTemplate(unittest.TestCase):
    """Tripwire: the shared template must keep declaring a UTM system."""

    def test_shared_template_declares_utm(self):
        self.assertEqual(params_template_frame(UTM_TEMPLATE), 'utm')


class TestUtmGeneration(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = write_flight_log_params(
                UTM_TEMPLATE, os.path.join(tmp, 'p53N.xml'), 53, 'N')
            content = read(out)
            self.assertIn('+proj=utm +zone=53 +datum=WGS84 +units=m +no_defs',
                          content)
            self.assertNotIn('+south', content)
            self.assertIn('epsg:32653 - WGS 84 / UTM zone 53N', content)
            self.assertEqual(params_template_frame(out), 'utm')
            # A generated file is itself a valid UTM template (round trip,
            # southern hemisphere this time).
            out2 = write_flight_log_params(
                out, os.path.join(tmp, 'p9L.xml'), 9, 'L')
            content2 = read(out2)
            self.assertIn('+proj=utm +zone=9 +south +datum=WGS84', content2)
            self.assertIn('epsg:32709 - WGS 84 / UTM zone 9S', content2)

    def test_the_format_id_survives_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = write_flight_log_params(
                UTM_TEMPLATE, os.path.join(tmp, 'p19T.xml'), 19, 'T')
            self.assertIn('{B438A617-2434-5A24-C1B7-58980F28345A}', read(out))
            self.assertIn('epsg:32619 - WGS 84 / UTM zone 19N', read(out))


class TestRequireUtmZone(unittest.TestCase):
    """The guard run before every trajectory import."""

    def test_tagged_names_yield_zone_and_band(self):
        self.assertEqual(require_utm_zone('flight_log_19T_UTM.txt'), (19, 'T'))
        self.assertEqual(
            require_utm_zone(os.path.join('zone_1', 'flight_log_57L_UTM.txt')),
            (57, 'L'))

    def test_untagged_names_are_refused(self):
        for name in ('flight_log_UTM.txt', 'flight_log.txt', 'nav.txt'):
            with self.assertRaises(ValueError) as ctx:
                require_utm_zone(name)
            self.assertIn('no UTM zone tag', str(ctx.exception))
            self.assertIn(name, str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
