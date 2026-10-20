"""m365-demo-seed: seed fictional Microsoft 365 work content into a demo tenant.

Standard library only (Python 3.10+). The administrator signs in for each run inside the
run's own process; tokens stay in memory, and the only stored secret is the demo users'
passwords, which the user types in their own terminal (owner-only file).
"""

VERSION = "0.1.0"
