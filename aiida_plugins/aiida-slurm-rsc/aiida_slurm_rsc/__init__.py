"""aiida-slurm-rsc: AiiDA Slurm scheduler plugin for KUDPC Camphor (sp)."""

from aiida_slurm_rsc.mail import MAIL_USER_PROPERTY, mail_scheduler_commands
from aiida_slurm_rsc.scheduler import SlurmRscScheduler

__all__ = ("MAIL_USER_PROPERTY", "SlurmRscScheduler", "mail_scheduler_commands")
__version__ = "0.1.0"
