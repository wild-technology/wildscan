"""FlightLogParams template selection for the align stage: an explicit
template wins, otherwise the repository's UTM template. Either way its zone
is rewritten from each flight log's own filename tag before import."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.realityscan_interface.realityscan_interface import (
    flight_log_params_template)

MD = os.path.join("some", "metadata")


def test_default_is_the_utm_template():
    assert flight_log_params_template(MD) == os.path.join(MD, "FlightLogParams.xml")
    assert flight_log_params_template(MD, None) == \
        os.path.join(MD, "FlightLogParams.xml")
    assert flight_log_params_template(MD, "") == \
        os.path.join(MD, "FlightLogParams.xml")


def test_explicit_template_wins():
    explicit = os.path.join("survey", "MyParams.xml")
    assert flight_log_params_template(MD, explicit) == explicit
