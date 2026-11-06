"""What interCEde knows about HTCondor, shared by every backend that drives it.

Plain functions and data only, with no dependency on any backend class: the
HTCondor-CE backend uses them directly, and the HTCondor scheduler of the
composed backend (issue #21) will use the same ones over its transport. They
share these functions, never a subtype.

- ``submit``: a submit description built from a ``SubmissionSpec``
- ``commands``: the argv of each HTCondor tool, and parsers for their output
- ``status``: the state mapping from job ads to ``JobStatus``
"""
