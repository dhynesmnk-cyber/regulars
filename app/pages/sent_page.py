"""Sent screen: the exported batches, and the append-only ledger behind them."""

from exports import batches
from state import STATE
from ui import e, page, stat
from views import _flash_html, money
from web import html


def page_sent():
    body = [_flash_html(), '<h1>Sent.</h1>',
            '<p class="lead">Every batch you&rsquo;ve exported: who was in it, the exact words, '
            'who approved it, on what consent basis, when. Two jobs: an evidence trail if anyone '
            'ever asks, and the feedback that makes the next batch better.</p>']
    if not STATE["sent"]:
        body.append('<div class="card"><h3>Nothing exported yet</h3>'
                    '<p class="muted sm">On <a href="/inbox">Messages</a>, approve the ones you '
                    'want to send and choose Export. The batch appears here with its file, which '
                    'you import into your own email tool. Regulars never sends email itself.'
                    '</p></div>')
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

    done = batches()
    body.append('<div class="grid g3">')
    body.append(stat("Exported", f'{len(STATE["sent"])}',
                     f'in {len(done)} batch{"es" if len(done) != 1 else ""}'))
    body.append(stat("Offer cost", money(sum(x["offer_cost"] for x in STATE["sent"]), 2),
                     "if all redeem"))
    body.append(stat("Contacts", f'{len({x["customer_id"] for x in STATE["sent"]})}', "unique"))
    body.append('</div>')

    groups = [(bid, xs) for bid, xs in reversed(list(done.items()))]
    older = [x for x in STATE["sent"] if not x.get("batch")]
    if older:
        groups.append((None, older))
    for bid, xs in groups:
        if bid:
            held = sum(1 for x in xs if x.get("holdout"))
            body.append(f'<h2>Batch of {len(xs)} &middot; {e(xs[0].get("exported_at", ""))}</h2>')
            body.append(f'<div class="row between" style="margin:0 0 20px"><span class="sm muted">'
                        f'{held} marked Holdout: leave them out when you send.</span><span>'
                        f'<a class="btn sm" href="/export.csv?batch={e(bid)}">Download the CSV</a> '
                        f'<a class="btn sm ghost" href="/export-copy.txt?batch={e(bid)}">Copy as '
                        f'plain text</a></span></div>')
        else:
            body.append('<h2>Approved before batches</h2>')
        body.append('<div class="card"><table><thead><tr><th>Customer</th><th>Trigger</th>'
                    '<th>Offer</th><th>Subject</th><th>Approved</th><th class="r">Cost</th></tr>'
                    '</thead><tbody>')
        for x in xs:
            body.append(f'<tr><td><b>{e(x["name"])}</b><br><span class="sm soft">{e(x["email"])}'
                        f'</span></td><td class="sm">{e(x["trigger_name"])}</td>'
                        f'<td class="sm">{e(x["offer_label"])}</td>'
                        f'<td class="sm">{e(x["subject"])}</td>'
                        f'<td class="sm">{e(x["approved_at"])}<br>'
                        f'<span class="soft">{e(x["approver"])}</span></td>'
                        f'<td class="r">{money(x["offer_cost"], 2)}</td></tr>')
        body.append('</tbody></table></div>')
    body.append('<div class="good sm">Each entry records the customer ID, the exact message '
                'content, the trigger that produced it, the consent basis and a timestamp. That '
                'record is append-only. It is what you produce if ACMA ever asks how you got '
                'consent, and it is why nothing in this product sends automatically.</div>')
    return page("Sent", "".join(body), "/sent")


def get_sent(req):
    return html(page_sent())
