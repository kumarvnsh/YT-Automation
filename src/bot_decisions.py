"""Bounded owner choices from reports; never executable model actions."""
import re


def clean(value, limit):
    return ' '.join(re.sub(r'[\x00-\x1f\x7f]', ' ', str(value or '')).split())[:limit]


def validate(raw):
    result=[]; seen=set()
    if not isinstance(raw,list):return result
    for item in raw[:10]:
        if not isinstance(item,dict):continue
        key=re.sub(r'[^a-z0-9]+','-',str(item.get('key','')).lower()).strip('-')[:60]
        options=[]; ids=set()
        for option in (item.get('options') if isinstance(item.get('options'),list) else [])[:3]:
            if not isinstance(option,dict):continue
            oid=re.sub(r'[^a-z0-9_-]','',str(option.get('id','')).lower())[:32]
            label=clean(option.get('label'),80);plan=clean(option.get('plan'),600)
            if oid and oid not in ids and label and plan:
                options.append({'id':oid,'label':label,'plan':plan,'tradeoff':clean(option.get('tradeoff'),240)})
                ids.add(oid)
        title=clean(item.get('title'),140)
        if key and key not in seen and title and len(options)>=2:
            result.append({'key':key,'title':title,'summary':clean(item.get('summary'),400),
                'evidence':clean(item.get('evidence'),600),'success_metric':clean(item.get('success_metric'),240),
                'options':options})
            seen.add(key)
        if len(result)>=3:break
    return result


def from_report(task):
    """Offer an explicit report proposal verbatim, without guessing settings/actions."""
    text=task.get('result') or ''
    headings=list(re.finditer(r'(?im)^[ \t]*(?:#{1,3}[ \t]+)?(?:\*\*)?(Findings|Evidence|Next experiment|Success metric|Limitations)(?:\*\*)?[ \t]*:?[ \t]*$',text))
    sections={}
    for i,heading in enumerate(headings):
        sections[heading.group(1).lower()]=text[heading.end():headings[i+1].start() if i+1<len(headings) else len(text)].strip()
    plan=sections.get('next experiment','')
    if len(plan)<30 or re.match(r'(?i)^(no (?:new )?experiment|continue (?:the )?(?:current|existing|approved))',plan):return None
    return {'key':'report-proposal-'+task['id'],'title':'Prepare this experiment or keep the current approach?',
        'summary':'A proposal from this report. Choosing records your direction and queues a detailed brief; it does not change production settings.',
        'evidence':clean(sections.get('evidence') or sections.get('findings'),600),
        'success_metric':clean(sections.get('success metric'),240),
        'options':[{'id':'prepare','label':'Prepare the proposed experiment','plan':clean(plan,600),
                    'tradeoff':'Needs an implementation brief and checks before any production change.'},
                   {'id':'keep-current','label':'Keep the current approach',
                    'plan':'Keep the current production approach and collect more comparable evidence before introducing this proposed experiment.',
                    'tradeoff':'Avoids disrupting current work; the proposed test is deferred.'}]}


PROMPT=(' Include an optional "decisions" array in FINDINGS_JSON when a genuine owner choice is needed: '
    '[{"key":"stable-choice-key","title":"decision question","summary":"why it needs a choice",'
    '"evidence":"support from this snapshot","success_metric":"how to assess the result",'
    '"options":[{"id":"option-a","label":"short choice","plan":"specific proposed direction",'
    '"tradeoff":"cost, limitation or uncertainty"},{"id":"option-b","label":"alternative",'
    '"plan":"alternative direction","tradeoff":"tradeoff"}]}]. '
    'Return at most three decisions, two or three distinct options each. Reuse the same key for the same '
    'question. Do not manufacture decisions, ask to approve already-authorized routine work, or invent evidence. '
    'An owner selection records a strategy preference and queues an implementation brief; it does not '
    'authorize arbitrary commands or silently change production settings. Use [] when no decision is needed.')
