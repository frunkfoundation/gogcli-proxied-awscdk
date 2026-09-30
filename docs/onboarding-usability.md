# Onboarding usability contract

The outcome is a usable Google account, then the same result for the other
accounts. Importing a client, saving setup, and connecting an account are distinct
milestones. The page must tell the owner which one has happened and what to do next.

| Guidance | Rule applied here |
| --- | --- |
| [Nielsen: match with the real world](https://www.nngroup.com/articles/ten-usability-heuristics/) | Order the steps by their dependencies: enable APIs, configure the Google app, import its Web client, review that client, connect accounts. |
| [Nielsen: visibility of status](https://www.nngroup.com/articles/ten-usability-heuristics/) | Derive setup readiness from the saved current client and API confirmations. Keep account connection counts separate from setup progress. |
| [Nielsen: recognition rather than recall](https://www.nngroup.com/articles/ten-usability-heuristics/) | Show current project, missing requirements, and the next action together. Supply Console field values and direct links. |
| [Nielsen: error prevention and recovery](https://www.nngroup.com/articles/ten-usability-heuristics/), [GOV.UK error summary](https://design-system.service.gov.uk/components/error-summary/) | Name missing API reviews at submission, open their section, and focus the first relevant control. Do not give an incomplete setup a success message. |
| [GOV.UK task list](https://design-system.service.gov.uk/components/task-list/) | Keep this dependent sequence ordered and resumable. A generic task-list interface is unsuitable when tasks require a specific order. |

Review checkboxes follow the client upload because a new client revision resets
its confirmations. Do not silently copy approval from another revision to make
the UI appear complete. Refreshes preserve unsaved drafts and open sections;
saved state still governs whether an account can start consent.

Successful review ends with an explicit Connect your accounts action. It scrolls
and focuses an eligible account's button; the owner still chooses to start Google
consent. Blocked accounts name the prerequisites relevant to their chosen project
and services. Installed connections retain check, repair, and access controls.

The browser acceptance journey starts with no imported client and exercises
upload, incomplete review, complete review, the account handoff, and Google consent
through the real owner HTTP handler. Additional checks cover revision changes,
refresh/reopen, multiple accounts, keyboard focus, and mobile layout. These checks
verify behavior; they do not replace observation of a real user's Google journey.
