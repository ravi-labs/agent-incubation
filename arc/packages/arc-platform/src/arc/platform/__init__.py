"""arc.platform — web platform for the arc agent incubation system.

Two React frontends, one shared FastAPI backend:

  - ``frontend/ops``   business-user dashboard: approval queue, agent
                       inventory, lifecycle stages, audit trail. Built
                       with Vite + React + TypeScript.

  - ``frontend/dev``   engineer dashboard: harness runs, eval results,
                       agent dev workflow, debugging. Built with the
                       same stack.

Both apps consume the same FastAPI JSON API (``arc.platform.api``)
and share types + an API client via the ``@arc/shared`` workspace
package under ``frontend/shared``.

Launch the backend via the CLI:

    arc platform serve              # FastAPI on port 8000

Run a frontend in dev mode (from frontend/ops or frontend/dev):

    npm install
    npm run dev                     # Vite dev server with API proxy
"""

__all__ = []
