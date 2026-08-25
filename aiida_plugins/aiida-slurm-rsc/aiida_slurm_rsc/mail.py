"""Mail notification for jobs submitted through :mod:`aiida_slurm_rsc.scheduler`.

aiida-core has no option for this. ``CalcJob.presubmit`` never fills in
``JobTemplate.email`` -- the line that would is commented out -- and
``metadata.options`` is a static port namespace, so a scheduler plugin cannot
add an option to it either. What aiida-core *does* offer is
``metadata.options.custom_scheduler_commands``: a per-calculation string
appended verbatim to the submit-script header. That is the supported way to
reach ``sbatch`` with something AiiDA has no opinion about, so the settings are
passed as an ordinary argument and rendered into it here::

    builder.metadata.options.custom_scheduler_commands = mail_scheduler_commands(
        computer, terminal=True
    )

The event policy comes from the group's ``slurm-async-runner`` pipeline: every
step reports its own failure, and "the work is done" is a single message from
the terminal step rather than one per step.

The address is not an argument to every call. It belongs to the cluster
account, so it is stored once on the ``Computer``::

    computer.set_property(MAIL_USER_PROPERTY, "you@example.org")

which is aiida-core's own free-form per-computer metadata, kept in the profile
database. Nothing is hardcoded here and no address is committed: a computer
without the property mails nobody.
"""

from __future__ import annotations

from typing import Any, Iterable

__all__ = (
    "MAIL_USER_PROPERTY",
    "VALID_MAIL_TYPES",
    "mail_scheduler_commands",
)

#: ``Computer`` metadata key holding the default recipient.
MAIL_USER_PROPERTY = "mail_user"

#: Intermediate steps; the terminal one adds ``END``.
DEFAULT_MAIL_TYPES = ("FAIL",)
TERMINAL_MAIL_TYPES = ("END", "FAIL")

#: sbatch(1). A typo here would produce a job that quietly never mails, and a
#: notification that never arrives looks exactly like nothing having gone
#: wrong, so an unknown value has to fail at submission instead.
VALID_MAIL_TYPES = frozenset({
    "NONE", "BEGIN", "END", "FAIL", "REQUEUE", "ALL", "INVALID_DEPEND",
    "STAGE_OUT", "TIME_LIMIT", "TIME_LIMIT_90", "TIME_LIMIT_80",
    "TIME_LIMIT_50", "ARRAY_TASKS",
})


def resolve_recipient(recipient: Any) -> str:
    """Return the address for ``recipient``: a string, or a ``Computer``.

    A ``Computer`` is asked for its :data:`MAIL_USER_PROPERTY`; one that does
    not carry it returns ``""``, which means "no mail" rather than an error --
    notification is opt-in, and a computer set up before anyone thought about
    mail should keep submitting.
    """
    if recipient is None:
        return ""
    get_property = getattr(recipient, "get_property", None)
    if get_property is not None:
        return (get_property(MAIL_USER_PROPERTY, "") or "").strip()
    return str(recipient).strip()


def normalise_mail_types(mail_types: Iterable[str]) -> list[str]:
    """Upper-case and validate ``mail_types``, preserving their order."""
    types = [t.strip().upper() for t in mail_types if t and t.strip()]
    unknown = sorted(set(types) - VALID_MAIL_TYPES)
    if unknown:
        raise ValueError(
            f"unknown Slurm mail type(s) {unknown}; "
            f"valid values are {sorted(VALID_MAIL_TYPES)}"
        )
    return types


def mail_scheduler_commands(
    recipient: Any,
    *,
    terminal: bool = False,
    mail_types: Iterable[str] | None = None,
) -> str:
    """Return ``#SBATCH --mail-*`` lines for ``metadata.options.custom_scheduler_commands``.

    :param recipient: an address, or a ``Computer`` carrying
        :data:`MAIL_USER_PROPERTY`. Anything falsy means no mail.
    :param terminal: the last step of a piece of work, which also reports
        success (``END,FAIL``). Intermediate steps report failure only.
    :param mail_types: override the event list outright, e.g. ``["BEGIN",
        "END"]``. ``["NONE"]`` turns notification off for one calculation.
    :return: the header lines, or ``""`` when nothing should be sent -- which
        is a valid value for the option and leaves the header untouched.
    """
    address = resolve_recipient(recipient)
    if not address:
        return ""

    if mail_types is None:
        types = list(TERMINAL_MAIL_TYPES if terminal else DEFAULT_MAIL_TYPES)
    else:
        types = normalise_mail_types(mail_types)

    if not types or types == ["NONE"]:
        return ""

    return "\n".join((
        f"#SBATCH --mail-user={address}",
        f"#SBATCH --mail-type={','.join(types)}",
    ))
