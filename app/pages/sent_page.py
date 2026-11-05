"""Sent screen: the append-only approval ledger."""

from state import STATE
from ui import e, page, stat
from views import money
from web import html


def page_sent():
    body = ['<h1>Sent.</h1>',
            '<p class="lead">Every message, who approved it, on what consent basis, when. Two jobs: '
            'an evidence trail if anyone ever asks, and the feedback that makes the next batch '
            'better.</p>']
    if not STATE["sent"]:
        body.append('<div class="card"><h3>Nothing sent yet</h3>'
                    '<p class="muted sm">Approve messages from the list and they&rsquo;ll appear '
                    'here. This prototype transmits nothing &mdash; in production this pushes to '
                    'the sender you configured, or you export a CSV and upload it yourself.</p></div>')
        body.append('<h2>How measurement will work</h2><ol class="steps">')
        for t, d in [
            ("Hold back 10% automatically", "A random control group that gets nothing. Without it "
             "you cannot tell whether the campaign worked or whether those people were coming "
             "anyway. It is the single most-missed feature in hospitality marketing."),
            ("Match on customer, not on venue total", "Comparing the whole venue before and after "
             "a send overstates the effect massively, because it counts people who were never in "
             "the audience. We compare audience against holdout."),
            ("Measure at 21 days", "Long enough for a monthly regular to have had the chance to "
             "come in, short enough to act on the result."),
            ("Feed the result back into the rules", "After three campaigns you stop using generic "
             "response rates and start using yours. That is when the projections become "
             "believable, and it is the reason not to churn."),
        ]:
            body.append(f'<li><b>{t}</b><span>{d}</span></li>')
        body.append('</ol>')
        return page("Sent", "".join(body), "/sent")

    body.append('<div class="grid g3">')
    body.append(stat("Approved", f'{len(STATE["sent"])}', "this session"))
    body.append(stat("Offer cost", money(sum(x["offer_cost"] for x in STATE["sent"]), 2),
                     "if all redeem"))
    body.append(stat("Contacts", f'{len({x["customer_id"] for x in STATE["sent"]})}', "unique"))
    body.append('</div>')
    body.append('<div class="card"><table><thead><tr><th>Customer</th><th>Trigger</th><th>Offer</th>'
                '<th>Subject</th><th>Approved</th><th class="r">Cost</th></tr></thead><tbody>')
    for x in STATE["sent"]:
        body.append(f'<tr><td><b>{e(x["name"])}</b><br><span class="sm soft">{e(x["email"])}</span></td>'
                    f'<td class="sm">{e(x["trigger_name"])}</td><td class="sm">{e(x["offer_label"])}</td>'
                    f'<td class="sm">{e(x["subject"])}</td><td class="sm">{e(x["approved_at"])}<br>'
                    f'<span class="soft">{e(x["approver"])}</span></td>'
                    f'<td class="r">{money(x["offer_cost"], 2)}</td></tr>')
    body.append('</tbody></table></div>')
    body.append('<div class="good sm">Each approval records the customer ID, the exact message '
                'content, the trigger that produced it, the consent basis and a timestamp. That '
                'record is append-only. It is what you produce if ACMA ever asks how you got '
                'consent, and it is why nothing in this product sends automatically.</div>')
    return page("Sent", "".join(body), "/sent")


def get_sent(req):
    return html(page_sent())
