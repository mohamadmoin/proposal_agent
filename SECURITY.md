# Security policy

## Supported versions

Security fixes are made on the latest release and on `main`.

## Reporting a vulnerability

Please **do not open a public issue** for a security problem. Report it privately through GitHub's
"Report a vulnerability" button (Security → Advisories) on the repository, with:

- what the problem is and where (file, endpoint, configuration);
- how to reproduce it;
- what an attacker could do with it.

We aim to acknowledge a report within 5 working days and to agree a fix and a disclosure date with you.

## Running the app safely

- Never commit `.env` or any key. Set a long random `DJANGO_SECRET_KEY` and keep `DJANGO_DEBUG=0` outside development.
- Set `API_TOKENS` to long random values; the API is closed (403) without them unless debug is on.
- Put the app behind HTTPS (and set `DJANGO_BEHIND_HTTPS_PROXY=1`, `CSRF_TRUSTED_ORIGINS`) when it is reachable from
  the internet; use `WEB_ACCESS_CODE` to keep a demo private.
- Do not publish the MySQL port; the compose file keeps it internal.
- The NCBI API key is redacted from logs; model prompts and responses are stored in the database (`LLMCall`), so treat
  the database as sensitive and never enter patient data.
