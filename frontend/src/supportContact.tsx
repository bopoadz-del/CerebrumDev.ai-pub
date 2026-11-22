/**
 * The support/legal contact: at the foot of the Sessions list and on the
 * Account page (never in the phone header). It renders nothing until an
 * address is known, and the address comes from the operator's ``SUPPORT_EMAIL``
 * config (through ``/version``) — never from a hardcoded string here.
 */
export function SupportContact({ email }: { email: string | null }) {
  if (!email) return null
  return (
    <a className="rail-foot-support" href={`mailto:${email}`} aria-label="Contact support">
      {email}
    </a>
  )
}
