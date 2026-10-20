# Check the first-run experience

Status: **not yet measured by someone unfamiliar with the code**.
Automated tests and an implementation walkthrough are not a substitute for this
check. Issues #48 and #41 stay open until the required evidence is recorded.

Ask a new contributor to follow [DEMO.md](DEMO.md) and [AUTHORING.md](AUTHORING.md).
Measure installation separately. Start the task timer once dependencies and
Chromium are installed. Stop when the MCP client returns the correct book title
and price from the saved workflow.

Record:

- Date, operating system, Python version, and commit tested.
- Whether the contributor had used this code before.
- Installation time and any installation problems.
- Time from installed prerequisites to the first correct MCP result.
- Exact input and returned title and price.
- Any help, confusing prompt, manual selector edit, retry, or failed run.
- Whether the task took less than ten minutes.
- If it did not, the specific obstacle and a linked follow-up ticket.

Do not include personal details, credentials, or private browser state. Keep
live-site runs separate from controlled automated checks. For each live run,
record date, inputs, attempts, correct results, failures, and elapsed time.
The existing dated observations are in [PILOT.md](PILOT.md) and its linked evidence;
they predate guided authoring and do not prove this new journey.
