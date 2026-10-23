# interCEde

Unified interfaces to Computing Elements and batch systems for DIRAC / DiracX.

A consumer submits and monitors jobs through one set of protocols, whatever the resource behind
them. interCEde is stateless: it never changes a job on its own, and the consumer stores the job
handles it gets back.

- [User documentation](user/index.md): for code that uses interCEde, such as DiracX. Start with
  the [API reference](user/reference/api.md): the protocols, data types and exceptions a consumer
  may import.
- [Developer documentation](dev/index.md): for working on interCEde itself, including the
  integration test stacks.
