# Account connection usability contract

The page connects accounts to one application, gogcli-proxy. Application identity
appears once. Each account shows installed services, the current state, the next
useful action, and recovery. Google application provisioning is an operator task.

| Guidance | Rule applied here |
| --- | --- |
| [Nielsen: match with the real world](https://www.nngroup.com/articles/ten-usability-heuristics/) | Choose services, connect the named account at Google, then show connection checks. |
| [Nielsen: visibility of status](https://www.nngroup.com/articles/ten-usability-heuristics/) | Count successfully checked canonical connections; show existing access and failed replacements separately. |
| [Nielsen: recognition rather than recall](https://www.nngroup.com/articles/ten-usability-heuristics/) | Put the next action beside the account and explain paused access explicitly. |
| [Nielsen: error prevention and recovery](https://www.nngroup.com/articles/ten-usability-heuristics/), [GOV.UK error summary](https://design-system.service.gov.uk/components/error-summary/) | Prefer continuing consent or retrying saved authorization when evidence supports it; name the affected account and next action. |

Refresh preserves service drafts, account order, open options, and keyboard focus.
Returning from Google focuses the corresponding account result using the nonsecret
operation ID. Visiting or refreshing never starts consent. Durable server state
reconstructs progress after reopening. Paused access remains paused on reconnect.

Browser acceptance covers the real owner HTTP handler, service selection,
consent continuation/restart, canonical reconnection, saved recovery, pause and
disconnect, account addition, stable focus, and mobile layout. Observing a real
Google journey and actual caller reads remains part of deployment acceptance.
