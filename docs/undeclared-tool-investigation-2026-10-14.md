# Undeclared tool rejection investigation

The local event log for session `61f47b87-0e47-48ae-8845-3dfd78680ca1`, turn
`a6684569-e113-48a3-a40e-974084d345a5`, records three `deepseek-flash`
responses rejected at 14:16:29–14:16:38 UTC for an undeclared tool. The earlier
steps in that turn executed successfully. The request's schema plan omitted no
direct tools. This is a response contract rejection, not a transport failure.

The historical transcript contains `browser_eval`, but this continuation turn's
advertised tools do not: deferred activations are turn scoped. Calling a tool
from earlier history without searching again is one possible explanation.
The rejected name was not retained, so the evidence cannot establish that it
was specifically `browser_eval` or exclude an incorrect name from the model.

The rejection handler previously omitted the offending name and response ID
and truncated the available-tool list to its first twelve entries. This also
made repair feedback incomplete. It now retains the exact tool name, response
identity, stream diagnostics and complete advertised names, without retaining
tool argument values. It still rejects the entire batch before any execution;
it does not guess an alias or execute a tool absent from the request.

Validation: 69 tests in the tool argument contract, tool wire codec, response
protocol and deferred tool search suites pass. Coverage includes unknown-name
repair feedback, catalogs longer than twelve entries, argument-value omission,
and ensuring the otherwise valid calls in a rejected batch do not execute.

This change fixes the diagnostic and repair-feedback defect. It does not claim
to recover the missing historical name or prove that the live model will never
return an undeclared tool again.
