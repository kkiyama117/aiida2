"""mail_scheduler_commands(): the parameter that carries mail settings.

No AiiDA profile is needed. `recipient` is only ever asked for
`get_property`, so a stub stands in for a stored `Computer`.
"""

import pytest

from aiida_slurm_rsc.mail import (
    MAIL_USER_PROPERTY,
    mail_scheduler_commands,
    resolve_recipient,
)

ADDR = "someone@example.org"


class FakeComputer:
    """Just enough Computer to be a recipient (see aiida.orm.Computer)."""

    def __init__(self, metadata=None):
        self.metadata = metadata or {}

    def get_property(self, name, *args):
        try:
            return self.metadata[name]
        except KeyError:
            if not args:
                raise AttributeError(f"'{name}' property not found")
            return args[0]


def test_address_string():
    assert mail_scheduler_commands(ADDR) == (
        f"#SBATCH --mail-user={ADDR}\n#SBATCH --mail-type=FAIL"
    )


def test_intermediate_step_mails_failures_only():
    assert "--mail-type=FAIL" in mail_scheduler_commands(ADDR)


def test_terminal_step_also_mails_success():
    assert "--mail-type=END,FAIL" in mail_scheduler_commands(ADDR, terminal=True)


def test_computer_property_supplies_the_address():
    computer = FakeComputer({MAIL_USER_PROPERTY: ADDR})
    assert f"--mail-user={ADDR}" in mail_scheduler_commands(computer)


def test_computer_without_the_property_sends_nothing():
    """Notification is opt-in: a computer set up before it existed still works."""
    assert mail_scheduler_commands(FakeComputer()) == ""


@pytest.mark.parametrize("recipient", [None, "", "   "])
def test_no_address_sends_nothing(recipient):
    assert mail_scheduler_commands(recipient) == ""


def test_explicit_mail_types_override_the_policy():
    text = mail_scheduler_commands(ADDR, mail_types=["begin", "end"])
    assert "--mail-type=BEGIN,END" in text


def test_none_opts_one_calculation_out():
    assert mail_scheduler_commands(ADDR, mail_types=["NONE"]) == ""


def test_empty_mail_types_send_nothing():
    assert mail_scheduler_commands(ADDR, mail_types=[]) == ""


def test_unknown_mail_type_raises():
    with pytest.raises(ValueError, match="DONE"):
        mail_scheduler_commands(ADDR, mail_types=["FAIL", "DONE"])


def test_resolve_recipient_strips_whitespace():
    assert resolve_recipient(f"  {ADDR} ") == ADDR
