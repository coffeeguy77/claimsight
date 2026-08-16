"""Privacy policy and terms of service copy.

Written to describe what ClaimSight actually does, not a downloaded template. Two
things drive the content and both are deliberate:

**APP 8 — cross-border disclosure.** The application runs on Railway in the
`sfo` region, so personal information about Australian claimants is stored in
the United States. Australian Privacy Principle 8 requires that a privacy policy
state whether personal information is likely to be disclosed to overseas
recipients and, where practicable, which countries. Saying nothing is not an
option; the disclosure below is the honest position until the deployment region
changes, at which point this text must be updated to match.

**No claims that are not true.** No certifications, no encryption standards, no
retention guarantees beyond what the product enforces. Everything stated here
maps to something built and tested.

THIS IS A STARTING DRAFT, NOT LEGAL ADVICE. Neither document has been reviewed
by an Australian lawyer, and the placeholders below must be completed before
either page is relied on commercially.
"""

# Fill these in before the pages mean anything legally.
ENTITY = "Web Culture AI"
ABN = "[ABN]"
CONTACT_EMAIL = "[privacy@claimsight.com.au]"
POSTAL = "[postal address]"
UPDATED = "16 August 2026"

PRIVACY = f"""
<h1>Privacy policy</h1>
<div class="updated">Last updated {UPDATED}</div>

<div class="callout">
  <p><strong>In short.</strong> ClaimSight is a tool used by assessing firms to value
  contents claims. Most of the personal information in it is not ours and not about our
  customers — it is about the claimants whose losses are being assessed, and the assessing
  firm decides what goes in and who sees it. We store it, we do not sell it, and we do not
  use it to train AI models.</p>
</div>

<div class="toc">
  <ol>
    <li><a href="#who">Who we are</a></li>
    <li><a href="#roles">Our role, and your firm's role</a></li>
    <li><a href="#collect">What we collect</a></li>
    <li><a href="#use">How we use it</a></li>
    <li><a href="#ai">AI research and your claim data</a></li>
    <li><a href="#overseas">Where your data is stored</a></li>
    <li><a href="#share">Who we share it with</a></li>
    <li><a href="#security">Security</a></li>
    <li><a href="#retention">How long we keep it</a></li>
    <li><a href="#rights">Access, correction and complaints</a></li>
  </ol>
</div>

<h2 id="who">1. Who we are</h2>
<p>ClaimSight is operated by {ENTITY} (ABN {ABN}). In this policy, "we" and "us" means
{ENTITY}, and "you" means the assessing firm and its users.</p>
<p>Contact us about privacy at <strong>{CONTACT_EMAIL}</strong> or {POSTAL}.</p>

<h2 id="roles">2. Our role, and your firm's role</h2>
<p>There are two different kinds of personal information in ClaimSight and they are worth
separating.</p>
<h3>Your users</h3>
<p>Names, email addresses and passwords of the people at your firm who sign in. We collect
this directly and we are responsible for it.</p>
<h3>Claim data</h3>
<p>Claimants' names, risk addresses, insurer references, item inventories and photographs of
their property. Your firm decides what to upload, how long it stays, who at your firm can see
it and who a published report is sent to. We hold it on your behalf and act on your
instructions.</p>
<p>If you are a <strong>claimant</strong> and want to know what is held about you or have it
corrected, contact the assessing firm handling your claim. They control that record. If you
cannot identify or reach them, contact us and we will pass your request on.</p>

<h2 id="collect">3. What we collect</h2>
<ul>
  <li><strong>Account information</strong> — name, email address, organisation name, role, and a
      hashed password. We never store passwords in a readable form.</li>
  <li><strong>Claim content you upload</strong> — inventory spreadsheets, photo reports,
      photographs, receipts, quotes and any notes you add.</li>
  <li><strong>Assessment activity</strong> — every valuation, override, condition change, review
      and exclusion, recorded with the user who made it and when. This is the audit trail; it
      exists so assessments can be defended, and it cannot be edited or deleted.</li>
  <li><strong>Shared report access</strong> — when a published report link is opened we record the
      time, the browser type and the network address of the visitor, and whether a passcode
      attempt failed. This lets the assessing firm see whether a recipient opened the document.</li>
  <li><strong>Billing information</strong> — handled entirely by Stripe. We never see or store card
      numbers.</li>
</ul>

<h2 id="use">4. How we use it</h2>
<p>To operate the service: authenticating users, running valuations, generating reports,
serving shared links, metering claim volume against your plan, and taking payment. We also use
aggregate, non-identifying usage data to understand load and improve the product.</p>
<p>We do not sell personal information. We do not disclose it for anyone else's marketing.</p>

<h2 id="ai">5. AI research and your claim data</h2>
<p>Item valuation uses Anthropic's Claude API. When an item is valued, its description and
associated details are sent to Anthropic to identify the item and research replacement pricing,
and public web searches are performed to find current Australian retail prices.</p>
<p>Two consequences worth stating plainly. Item descriptions you enter <strong>leave our
systems</strong> when a valuation runs. And searches for replacement products are ordinary web
requests, so the retailers searched may log them as they would any visitor.</p>
<p>Claim data is not used to train AI models, by us or by Anthropic under its commercial API
terms. If you do not want a particular item's details sent for research, do not run automated
valuation on it — enter the value manually instead.</p>

<h2 id="overseas">6. Where your data is stored</h2>
<div class="callout">
  <p><strong>ClaimSight is hosted in the United States.</strong> The application and its database
  run on Railway infrastructure in a US region, and our AI research provider (Anthropic) and
  payment processor (Stripe) also process data outside Australia.</p>
  <p>This means personal information about Australian claimants — including names, addresses and
  photographs of their property — is stored and processed overseas. We tell you this because
  Australian Privacy Principle 8 requires it, and because you may need to tell your own clients
  and insurers.</p>
</div>
<p>Countries where your information may be handled: <strong>the United States</strong>.</p>

<h2 id="share">7. Who we share it with</h2>
<ul>
  <li><strong>Railway</strong> — application and database hosting (United States).</li>
  <li><strong>Anthropic</strong> — AI research on item descriptions (United States).</li>
  <li><strong>Stripe</strong> — subscription billing and tax invoicing.</li>
  <li><strong>Anyone you send a report to.</strong> Publishing a report link makes that frozen copy
      available to whoever holds the link, subject to any passcode and expiry you set. Choose
      recipients carefully; the document contains the claimant's personal information.</li>
</ul>
<p>We may also disclose information where required by law.</p>

<h2 id="security">8. Security</h2>
<p>What is in place today:</p>
<ul>
  <li>Traffic is encrypted in transit (HTTPS).</li>
  <li>Passwords are hashed, never stored readable.</li>
  <li>Each organisation's claims are separated; users cannot reach another firm's data.</li>
  <li>Role-based access — account holder, assessor, and read-only viewer — enforced on the server,
      not just hidden in the interface.</li>
  <li>Published report links can carry a passcode and an expiry, can be revoked, and every access
      attempt is logged.</li>
  <li>Every assessment change is recorded against the user who made it.</li>
</ul>
<p>We hold no security certifications and we do not claim any. No system is perfectly secure. If
you believe there has been unauthorised access, contact us at {CONTACT_EMAIL} immediately. Where
a data breach is likely to result in serious harm we will notify affected parties and the OAIC
as required by the Notifiable Data Breaches scheme.</p>

<h2 id="retention">9. How long we keep it</h2>
<p>Claim data is kept while your account is active, because assessments are commonly revisited
long after settlement. If you close your account, contact us to arrange export or deletion.
Backups may persist for a short period after deletion. Billing records are retained as long as
Australian tax law requires.</p>

<h2 id="rights">10. Access, correction and complaints</h2>
<p>You can ask us what personal information we hold about you and ask us to correct it. Email
{CONTACT_EMAIL}. We will respond within 30 days.</p>
<p>If you are unhappy with how we have handled your information, tell us first and we will try to
resolve it. If you remain dissatisfied you can complain to the Office of the Australian
Information Commissioner at <a href="https://www.oaic.gov.au">oaic.gov.au</a>.</p>

<h2>Changes</h2>
<p>We will update this page when our practices change and revise the date at the top. Material
changes affecting how claim data is handled will be notified to account holders by email.</p>
"""


TERMS = f"""
<h1>Terms of service</h1>
<div class="updated">Last updated {UPDATED}</div>

<div class="callout">
  <p><strong>The important one:</strong> ClaimSight researches and suggests replacement values.
  It does not make assessment decisions. Every figure is subject to the professional judgement
  of the assessor who signs the report, and that assessor remains responsible for it.</p>
</div>

<h2>1. Agreement</h2>
<p>These terms are between {ENTITY} (ABN {ABN}) and the organisation subscribing to ClaimSight.
By creating an account you accept them on behalf of your firm and confirm you are authorised
to do so.</p>

<h2>2. The service</h2>
<p>ClaimSight imports contents inventories and photographic evidence, pairs them, researches
current Australian replacement costs, calculates depreciation, and produces settlement
schedules and reports.</p>

<h2>3. AI-assisted valuations are suggestions</h2>
<p>Replacement values are produced by automated research and presented with sources and a
confidence rating so they can be verified. They are <strong>suggestions for assessor review</strong>,
not professional valuations.</p>
<p>You are responsible for reviewing every value before relying on it, for the accuracy of any
report you publish or send, and for the professional judgement applied to the claim. We do not
provide valuation, insurance, legal or financial advice, and we are not a party to any claim
assessed using the service.</p>

<h2>4. Your data</h2>
<p>You keep ownership of everything you upload. You grant us the limited licence needed to
store and process it in order to run the service. You are responsible for having the right to
upload claim information, including claimants' personal information and photographs, and for
complying with your own privacy obligations. Our handling of that data is set out in the
<a href="/privacy">privacy policy</a>.</p>

<h2>5. Accounts and access</h2>
<p>You are responsible for your users' accounts and for the actions taken under them. Seats are
unlimited on every plan, but accounts are personal — do not share credentials. Tell us promptly
if you believe an account has been compromised.</p>

<h2>6. Plans, claim allowances and payment</h2>
<p>Plans are priced by claim volume, not by user. A claim counts once, when it is created;
re-valuing items on an existing claim does not count again. Your allowance resets when each
invoice is paid. Claims beyond the allowance are charged at the published overage rate.</p>
<p>All prices are in Australian dollars and exclude GST, which is added at checkout. Payment is
processed by Stripe. Subscriptions renew monthly until cancelled.</p>

<h2>7. Trial</h2>
<p>New accounts include a limited trial covering a set number of claims. No card is required.
When the trial ends, choose a plan to continue creating claims; your existing data remains
accessible.</p>

<h2>8. Cancellation</h2>
<p>Cancel at any time through the billing portal. Cancellation takes effect at the end of the
current billing period and we do not refund part-months. Contact us before closing an account
if you need your claim data exported.</p>

<h2>9. Acceptable use</h2>
<p>Do not use ClaimSight to upload material you have no right to, attempt to access another
organisation's data, interfere with the service, or resell it as your own product without a
written agreement.</p>

<h2>10. Availability</h2>
<p>We aim to keep the service available but do not guarantee uninterrupted access. We may take it
down for maintenance and will avoid doing so during Australian business hours where practical.
We offer no service level agreement.</p>

<h2>11. Liability</h2>
<p>Nothing in these terms excludes rights under the Australian Consumer Law that cannot be
excluded. Where liability can be limited, ours is limited to resupplying the service or paying
the cost of resupply.</p>
<p>To the extent permitted by law, we are not liable for indirect or consequential loss, or for
loss arising from a settlement figure adopted without assessor review, from reliance on an
AI-suggested value that was not verified, or from a report shared with a recipient you chose.</p>

<h2>12. Changes to these terms</h2>
<p>We may update these terms. Material changes will be notified to account holders by email at
least 30 days before they take effect. Continuing to use the service after that means you accept
the change.</p>

<h2>13. Governing law</h2>
<p>These terms are governed by the laws of the Australian Capital Territory, and the courts of
that jurisdiction have non-exclusive jurisdiction.</p>

<h2>14. Contact</h2>
<p>{CONTACT_EMAIL} · {POSTAL}</p>
"""

PAGES = {
    "/privacy": {"title": "Privacy policy", "body": PRIVACY},
    "/terms": {"title": "Terms of service", "body": TERMS},
}
