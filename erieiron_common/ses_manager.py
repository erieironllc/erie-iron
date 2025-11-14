import logging
import time

from erieiron_autonomous_agent.models import CloudAccount


def check_ses_quota(cloud_account: CloudAccount):
    ses_client = cloud_account.get_service_client("ses")
    quota = ses_client.get_send_quota()
    sent = float(quota.get("SentLast24Hours", 0))
    max_send = float(quota.get("Max24HourSend", 0))
    if max_send > 0 and sent >= max_send:
        raise Exception(
            {
                "description": "SES send quota exhausted",
                "sent_last_24_hours": sent,
                "max_24_hour_send": max_send,
                "hint": "Request SES production access or switch to SES simulator addresses to continue sending email."
            }
        )


def manage_ses_domain_settings(
        cloud_account: CloudAccount,
        domain_name: str
):
    if not domain_name:
        return
    
    check_ses_quota(cloud_account)
    _wait_for_ses_dkim_success(cloud_account, domain_name)


def _wait_for_ses_dkim_success(
        cloud_account: CloudAccount,
        domain_name: str,
        poll_interval_seconds: int = 30,
        max_wait_minutes: int = 15
) -> None:
    """
    Wait for SES DKIM verification to reach SUCCESS for the given domain.

    This is robust to:
    - Region mismatches (defaults to us-west-2 if the config has no region)
    - SES API flavor mismatch (tries SESv2, then falls back to SESv1)
    - Transient API errors

    SUCCESS conditions (short-circuits the wait):
    - SESv2: DkimAttributes.Status == 'SUCCESS'
    - SESv1: GetIdentityDkimAttributes.DkimVerificationStatus == 'SUCCESS'
             OR GetIdentityVerificationAttributes.VerificationStatus == 'SUCCESS' (fallback)

    Raises AgentBlocked with context on timeout or terminal DKIM failure.
    """
    poll_interval_seconds = max(int(poll_interval_seconds or 5), 5)
    
    logging.info(
        f"Waiting for SES DKIM SUCCESS for {domain_name} in {cloud_account}"
        f"(poll={poll_interval_seconds}s, timeout={max_wait_minutes}m)."
    )
    
    deadline = time.time() + (max_wait_minutes * 60)
    last_status = None
    
    while True:
        if time.time() > deadline:
            raise Exception({
                "desc": f"Timed out waiting for SES DKIM verification to reach SUCCESS for {domain_name}",
                "dkim_status": last_status or "UNKNOWN",
                "domain": domain_name,
                "cloud_account": cloud_account.id
            })
        
        status = None
        signing_enabled = None
        tokens = []
        
        # ---- Try SESv2 first
        try:
            sesv2 = cloud_account.get_service_client("sesv2")
            v2_resp = sesv2.get_email_identity(EmailIdentity=domain_name)
            dkim_attrs = v2_resp.get("DkimAttributes") or {}
            status = (dkim_attrs.get("Status") or "").upper()
            tokens = dkim_attrs.get("Tokens") or []
            signing_enabled = dkim_attrs.get("SigningEnabled")
        except Exception as exc:
            # Normalize NotFound across possible client shapes; fall through to v1.
            msg = str(exc)
            if "NotFound" in msg or "NotFoundException" in msg:
                status = "NOT_FOUND_V2"
            else:
                logging.info(f"SESv2 get_email_identity error for {domain_name} in {cloud_account.id}: {exc}")
        
        # ---- Fall back to SESv1 if v2 could not confirm a SUCCESS
        if status in (None, "", "NOT_FOUND_V2"):
            try:
                sesv1 = cloud_account.get_service_client("ses")
                
                # Identity existence and basic verification status
                v1_ver = sesv1.get_identity_verification_attributes(
                    Identities=[domain_name]
                ).get("VerificationAttributes", {})
                ver_status = ((v1_ver.get(domain_name) or {}).get("VerificationStatus") or "").upper()
                
                # DKIM-specific status and tokens
                v1_dkim = sesv1.get_identity_dkim_attributes(
                    Identities=[domain_name]
                ).get("DkimAttributes", {})
                dkim_attrs = (v1_dkim.get(domain_name) or {})
                dkim_status = (dkim_attrs.get("DkimVerificationStatus") or "").upper()
                tokens = dkim_attrs.get("DkimTokens") or tokens
                
                # Prefer DKIM status; fall back to verification status if DKIM is unavailable
                status = dkim_status or ver_status or "NOT_FOUND"
            except Exception as exc:
                logging.info(f"SESv1 fallback error for {domain_name} in {cloud_account.id}: {exc}")
                # Keep status as-is; we'll loop again.
        
        # Log on status change
        if status != last_status:
            token_preview = ", ".join(tokens) if tokens else "<no tokens>"
            logging.info(
                f"SES DKIM wait status for {domain_name}: {status or 'UNKNOWN'}; "
                f"SigningEnabled={signing_enabled}; Tokens={token_preview}"
            )
            last_status = status
        
        # Terminal conditions
        if status in {"SUCCESS", "VERIFIED"}:
            logging.info(f"SES DKIM verification succeeded for {domain_name} in {cloud_account.id}.")
            return
        
        if status in {"FAILED", "TEMPORARY_FAILURE"}:
            raise Exception({
                "desc": f"SES DKIM verification {status.lower()} for {domain_name}",
                "dkim_status": status,
                "domain": domain_name,
                "cloud_account": cloud_account.id,
                "dkim_tokens": tokens
            })
        
        time.sleep(poll_interval_seconds)
