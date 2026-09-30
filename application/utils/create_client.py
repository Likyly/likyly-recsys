"""CLI to onboard a new client (tenant): creates its row and prints its two API keys
once (secret: full access, server-side only; public: restricted, safe client-side).

Only each key's hash is stored - if you lose a printed key, there is no way to recover
it, only to issue a new one for that client (see regenerate_secret_key/
regenerate_public_key in db.py, or the self-service /clients/me/regenerate-* endpoints).

Usage:
    python create_client.py "Acme Shop"
"""
import sys

from db import create_client, init_db


def main():
    if len(sys.argv) != 2:
        print("Usage: python create_client.py \"<client name>\"")
        sys.exit(1)

    init_db()
    client_id, secret_key, public_key = create_client(sys.argv[1])

    print(f"Client created: id={client_id}")
    print(f"Secret key (full access, server-side only - save it now): {secret_key}")
    print(f"Public key (restricted, safe for client-side JS - save it now): {public_key}")


if __name__ == "__main__":
    main()
