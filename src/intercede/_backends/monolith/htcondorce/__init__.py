"""An HTCondor-CE, reached as a remote schedd (issue #23).

The backend drives the HTCondor command-line tools, as DIRAC's
``HTCondorCEComputingElement`` does with ``UseLocalSchedd = False``: jobs are
submitted with ``condor_submit -remote`` and their inputs spooled to the CE at
submit time, so no HTCondor daemon runs on the submitting host.

- ``backend``: the backend, joining the modules of its capabilities
- ``jobs``: which handles are ours, and what the CE knows of them (status)
- ``submit``: submitting, a capability of its own
- ``session``: one operation's credentials, and the tools run with them
- ``staging``: the private working area, and what a submission puts in it
- ``collect``: moving collected output safely into ``dest``
- ``process``: running one tool as a subprocess

What HTCondor itself knows (submit descriptions, commands, the state mapping)
lives in ``_backends._htcondor`` and is shared with the HTCondor scheduler of
the composed backend. This package holds only what is specific to a remote CE.
"""

from __future__ import annotations

from intercede._backends.monolith.htcondorce.backend import HTCondorCEBackend

__all__ = ["HTCondorCEBackend"]
