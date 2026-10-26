"""Explicitly approved operational-notification recipient, independent of SMTP login."""
OWNER_EMAIL = '111715@koreainvestment.com'
ERROR_EMAIL = 'myboy3743@gmail.com'  # Standalone error/cookie/status alerts.
OWNER_REPORT_GROUP = 'risk_aigent@googlegroups.com'  # Owner-confirmed membership.


def report_recipients(notify, cc):
    """Use the owner's known group route instead of a second personal copy.

    Do not infer membership in other groups. Without this group, retain the
    existing owner route. Explicit owner entries in notify/CC must not bypass
    the same-person duplicate guard; all other recipients stay as configured.
    """
    configured = list(notify) + list(cc)
    via_group = OWNER_REPORT_GROUP.casefold() in {a.casefold() for a in configured}
    targets = [OWNER_EMAIL] + configured
    if via_group:
        targets = [OWNER_REPORT_GROUP if a.casefold() == OWNER_REPORT_GROUP.casefold() else a
                   for a in targets if a.casefold() != OWNER_EMAIL.casefold()]
    return list(dict.fromkeys(targets))
