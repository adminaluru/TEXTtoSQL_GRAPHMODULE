def reset_session(conn) -> None:
    """
    Runs whenever a connection goes back to the pool (Step 7).

    The executor only uses transaction-scoped settings, so nothing should
    remain; RESET ALL makes sure no session setting can ever carry over to
    another caller's request.
    """
    conn.execute("RESET ALL")
    conn.commit()
