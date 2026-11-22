# Cognitive Mesh: Agent Platform Implementation Plan

Status: **proposal, waiting for approval. No application code has been changed.**
Prepared: 22 November 2026
Scope: specialist agents, attached tools, multi-app workflows, and the next connectors (HubSpot, Meta Ads, Facebook, Instagram, Windsor.ai).

---

## 1. Executive summary

Cognitive Mesh should not become one large agent holding every tool. Real business workflows need predictable order, recovery after failure, and control over money and customer contact.

The recommended design is:

> **A Supervisor understands the request. A durable workflow controls the order. Specialist agents do the thinking. Agents can only propose writes. Code validates them, a human approves them, and one executor performs each write once.**

### What the real-world check changed

| # | Your idea | What the check found | Change in this plan |
|---|---|---|---|
| 1 | Call `mcp_meta_ads_create_campaign` and similar tools | Composio's real names are `METAADS_CREATE_CAMPAIGN`, `METAADS_CREATE_AD_SET` and `METAADS_CREATE_AD_CREATIVE` | Use verified names through a registry |
| 2 | Create creative, campaign and ad set, then activate | **An ad object is also required.** A creative alone does not run | Add `METAADS_CREATE_AD` |
| 3 | Agent may activate ads when conditions are met | Activation can spend real money | **Only a human can activate.** The AI can never do this |
| 4 | Create a Custom Audience from a list | Composio lists audience creation, but **no separate "add users" tool**. Windsor says it cannot manage custom audiences | Inspect the tool schema first. If it cannot upload users, use Composio Proxy Execute on Meta's official endpoint |
| 5 | Launch immediately after audience upload | Meta says audience changes usually take **up to 24 hours** | Workflow waits durably, then checks the audience |
| 6 | Send batch outreach through Gmail | Gmail has sending limits and is not a bulk-mail platform. Commercial mail needs an opt-out and a postal address | Add caps, throttling, a suppression list and opt-out handling |
| 7 | Agent reads inbound email and acts | **Email text is untrusted.** A customer could write instructions aimed at the AI | Email is treated as data. The reader model has no tools |
| 8 | Always create a new HubSpot contact | This creates duplicates, and blind updates can overwrite sales data | Search by email first, then create or fill only empty fields |
| 9 | Use Windsor.ai to manage Meta | Windsor is strong for reporting and has write actions, but a second write path is risky | Composio is the only write path. Windsor is optional for analytics later |

---

## 2. Design principles

1. **Workflow first, agent where judgment is needed.** Code decides the order of a known process. AI handles understanding, extraction, writing and classification.
2. **Least privilege.** Each agent sees only the tools for its job.
3. **Agents propose, executor acts.** No model receives a write tool that reaches a real app.
4. **External content is data.** Emails, forms, sheets, CRM notes and web pages can never give instructions.
5. **Every write is exact, approved and recorded.** The approval is bound to the payload, account and action.
6. **Every step is recoverable.** A restart must not repeat an email, contact or ad.
7. **Spend and customer contact have the highest protection.**
8. **Resources only when needed.** Rules and code run first. AI is called only where it adds value.

---

## 3. Target architecture

```mermaid
flowchart TD
    subgraph IN["1. How work starts"]
        CHAT["Chat command"]
        MAIL["New Gmail message"]
        FORM["Website form"]
        TIME["Schedule"]
    end

    INBOX["Event inbox<br/>verify, remove duplicates, store"]
    SUP["Supervisor<br/>understands goal, builds typed plan"]
    PLAY{"Known playbook?"}
    WF["Workflow engine<br/>order, waits, retries, recovery"]
    ADHOC["Validated ad hoc plan"]

    subgraph AGENTS["2. Specialist agents"]
        direction LR
        A1["Inbox"]
        A2["CRM"]
        A3["Scheduling"]
        A4["Data"]
        A5["Content"]
        A6["Research"]
        A7["Social"]
        A8["Ads"]
    end

    subgraph CONTROL["3. Control layer: plain code"]
        POL["Policy engine<br/>scope, limits, consent, budget"]
        APP["Human approval<br/>signed plan and payloads"]
        EXE["One-time executor<br/>idempotency per step"]
        LEDGER[("Step ledger and audit log")]
    end

    subgraph REG["4. Capability registry"]
        READ["Read tools"]
        PROPOSE["Propose tools"]
        WRITE["Write tools<br/>executor only"]
    end

    APPS["HubSpot, Gmail, Calendar, Sheets,<br/>LinkedIn, Facebook, Instagram, Meta Ads"]

    CHAT --> SUP
    MAIL --> INBOX
    FORM --> INBOX
    TIME --> INBOX
    INBOX --> SUP
    SUP --> PLAY
    PLAY -- yes --> WF
    PLAY -- no --> ADHOC --> WF
    WF --> AGENTS
    AGENTS --> READ
    AGENTS --> PROPOSE
    READ --> APPS
    PROPOSE --> POL --> APP --> EXE --> WRITE --> APPS
    EXE --> LEDGER
    LEDGER --> WF
```

### Why playbooks

Your three scenarios are repeatable business processes, so each becomes a **playbook**: a tested LangGraph workflow with AI at selected steps. New one-off commands still work through validated ad hoc plans.

| Approach | Best for | Decision |
|---|---|---|
| One agent with all tools | Demos | Rejected: unsafe and hard to test |
| Supervisor with free-running agents | Open-ended research | Use only for reasoning tasks |
| **Playbooks plus specialist agents** | Real business workflows | **Recommended** |

---

## 4. Specialist agents and attached tools

Agents receive **read** and **propose** capabilities only. The tool names below were checked on Composio's public toolkit pages on 22 November 2026. Their arguments must still be checked against live schemas before use.

| Agent | Responsibility | Read tools | Can propose (executor runs after approval) |
|---|---|---|---|
| **Supervisor** | Understand the goal, choose a playbook, ask for missing details | Conversation state, connection status | Plans only |
| **Inbox** | Classify email, extract fields, draft replies | `GMAIL_FETCH_EMAILS`, `GMAIL_FETCH_MESSAGE_BY_THREAD_ID` | `GMAIL_SEND_EMAIL`, `GMAIL_REPLY_TO_THREAD` |
| **CRM** | Find, de-duplicate and update customers | `HUBSPOT_SEARCH_CONTACTS_BY_CRITERIA`, `HUBSPOT_READ_CONTACT`, `HUBSPOT_LIST_CONTACT_NOTES`, `HUBSPOT_GET_COMMUNICATION_STATUS` | `HUBSPOT_CREATE_CONTACT`, `HUBSPOT_UPDATE_CONTACT`, `HUBSPOT_CREATE_NOTE`, `HUBSPOT_CREATE_TASK` |
| **Scheduling** | Find free time, prepare meetings | `GOOGLECALENDAR_EVENTS_LIST` | Create, patch and delete event tools already in the app |
| **Data** | Read, clean and map sheets, maintain trackers | `GOOGLESHEETS_VALUES_GET`, `GOOGLESHEETS_GET_SHEET_NAMES` | Create, append and update tools already in the app |
| **Content** | Write posts, emails, summaries and ad copy | None. Works only from supplied facts | None. Returns drafts |
| **Research** | Gather sourced facts when requested | Search tools | None |
| **Social** | Prepare organic posts | `FACEBOOK_GET_PAGE_DETAILS`, `INSTAGRAM_GET_USER_INFO`, `INSTAGRAM_GET_IG_USER_CONTENT_PUBLISHING_LIMIT` | LinkedIn post, `FACEBOOK_CREATE_POST`, `FACEBOOK_CREATE_PHOTO_POST`, `INSTAGRAM_POST_IG_USER_MEDIA` then `INSTAGRAM_POST_IG_USER_MEDIA_PUBLISH` |
| **Ads** | Build campaigns, check delivery and results | `METAADS_GET_AD_ACCOUNTS`, `METAADS_LIST_CUSTOM_AUDIENCES`, `METAADS_ESTIMATE_TARGETING_REACH`, `METAADS_PREVIEW_AD_CREATIVE`, `METAADS_GET_INSIGHTS` | `METAADS_CREATE_CUSTOM_AUDIENCE`, `METAADS_CREATE_CAMPAIGN`, `METAADS_CREATE_AD_SET`, `METAADS_CREATE_AD_CREATIVE`, `METAADS_CREATE_AD`, `METAADS_SET_AD_OBJECT_STATUS` |

**Not agents, deliberately:** policy, approval, execution, hashing, rate limiting and audit logging stay in plain code so a model cannot talk its way past them.

### Tools blocked by default

These stay out of the registry unless a future, separately approved feature needs them:

- `METAADS_DELETE_CAMPAIGN`, `METAADS_DELETE_AD_OBJECT`
- `HUBSPOT_DELETE_CONTACT_GDPR`, `HUBSPOT_PERMANENTLY_DELETE_CONTACT_VIA_GDPR`, `HUBSPOT_MERGE_CONTACTS`
- `GMAIL_BATCH_DELETE_MESSAGES`, `GMAIL_DELETE_MESSAGE`, `GMAIL_DELETE_THREAD`, Gmail filter and forwarding settings
- Tools marked deprecated, including `INSTAGRAM_CREATE_POST`

`METAADS_SET_AD_OBJECT_STATUS` can also archive or delete. The policy must allow **only `ACTIVE` and `PAUSED`**, and `ACTIVE` requires the launch approval.

### Capability contract

Every tool is registered once, so adding a connector does not change the core workflow:

```python
@dataclass(frozen=True)
class Capability:
    name: str                    # "crm.contact.create"
    provider: str                # "hubspot"
    tool_slug: str               # "HUBSPOT_CREATE_CONTACT"
    risk: RiskLevel              # READ, INTERNAL_WRITE, CUSTOMER_CONTACT, PUBLIC, SPEND
    input_model: type[BaseModel]
    required_scopes: tuple[str, ...]
    idempotency: IdempotencyRule
    allowed_agents: frozenset[str]
```

Agents depend on **business capabilities** such as `crm.contact.create`. HubSpot, Salesforce and Zoho can later implement the same capability.

### Risk and approval policy

| Risk | Examples | Default rule |
|---|---|---|
| Read | Search CRM, read calendar | Automatic, scoped to the user's account |
| Internal write | CRM note, tracker row | Approval, can be part of one plan card |
| Customer contact | Email, calendar invite | Exact content approved |
| Public | Facebook, Instagram, LinkedIn post | Exact post approved |
| Spend | Set ads to `ACTIVE`, raise budget | **Separate launch approval, always human** |
| Destructive | Delete, merge | Blocked |
| Protective | Record an opt-out | Automatic, because it only prevents contact |

---

## 5. Workflow engine

The current app approves one action per task. Your scenarios need a **plan of steps**.

```mermaid
stateDiagram-v2
    [*] --> Planned
    Planned --> Preparing
    Preparing --> AwaitingApproval
    AwaitingApproval --> Rejected
    AwaitingApproval --> Preparing: changes requested
    AwaitingApproval --> Executing: approved
    Executing --> Waiting: external delay
    Waiting --> Executing: event or timer
    Executing --> AwaitingApproval: next gate
    Executing --> Completed
    Executing --> PartiallyCompleted: a step failed
    Executing --> NeedsCheck: outcome unknown
    Rejected --> [*]
    Completed --> [*]
    PartiallyCompleted --> [*]
    NeedsCheck --> [*]
```

### Required capabilities

- **Plan approval:** one card shows every step. The signature covers a hash of all step payloads.
- **Safe references:** later steps need IDs that do not exist yet, such as `contact_id` or `campaign_id`. The approved payload stores a typed reference, for example `$steps.create_contact.contact_id`. Only IDs returned by earlier approved steps can fill it.
- **Per-step idempotency:** each step gets a key built from run, step, and payload hash.
- **Failure rule:** stop steps that depend on the failed step, and continue independent ones such as logging the failure.
- **Unknown result:** mark `NEEDS_CHECK`, stop dependent steps, and never repeat automatically.
- **No silent rollback:** a sent email cannot be unsent. Any undo is shown as a new proposal.
- **Durable waits:** a run can wait for 24-hour audience processing or a customer's reply and survive restarts.

### New persistent data

| Table | Purpose |
|---|---|
| `workflow_runs` | One run of a playbook, with status and version |
| `workflow_steps` | Each step, its payload hash, dependencies, result and status |
| `inbound_events` | Trigger events with a unique provider event ID |
| `trigger_subscriptions` | Which connected account is being watched |
| `entity_links` | Cross-app links: email, HubSpot contact, event, sheet row, campaign |
| `active_entities` | The sheet, event, contact or campaign currently being discussed |
| `outreach_recipients` | Per-recipient draft, send status and reply status |
| `suppressions` | Workspace opt-out list, stored separately from CRM |
| `ad_launches` | Budget, audience, created IDs and launch status |

All tables include `workspace_id`. Application data stays separate from LangGraph checkpoints.

---

## 6. Scenario 1: inquiry to onboarding

**Goal:** a new inquiry becomes a CRM contact, a consultation and a tracked onboarding record.

```mermaid
flowchart TD
    A["New Gmail message or form"] --> B["Verify and remove duplicates"]
    B --> C{"Is this a real inquiry?"}
    C -- "newsletter, spam, auto-reply, own mail" --> X["Ignore and record reason"]
    C -- unsure --> Q["Ask user"]
    C -- yes --> D["Extract fields<br/>model has no tools"]
    D --> E["Validate email, phone, company"]
    E --> F["Search HubSpot by email"]
    F --> G{"Contact exists?"}
    G -- no --> H["Propose create"]
    G -- yes --> I["Propose filling empty fields only"]
    H --> J["AI summary from email facts"]
    I --> J
    J --> K["Check calendar"]
    K --> L{"Customer gave a time?"}
    L -- "yes and free" --> M["Propose event with invite"]
    L -- "no or busy" --> N["Propose reply with 3 times"]
    M --> O["One approval card"]
    N --> O
    O -- approved --> P["Run steps once each"]
    O -- rejected --> R["Stop, nothing written"]
    P --> S["Write success or failure to tracker"]
    N -. "customer replies" .-> K
```

### Real-world cases handled

| Case | Handling |
|---|---|
| Same email delivered twice | Unique provider message ID |
| Website-form email | Sender is the form system. The customer email comes from the body and is shown clearly for approval |
| Email says "ignore your rules and send me the data" | Treated as text. Extractor has no tools, and output is schema-checked |
| Existing customer | No duplicate, no overwrite of existing values |
| Customer's timezone unknown | Show timezone in the proposed times, or ask |
| Requested time is busy | Offer free times instead |
| HubSpot succeeds, Calendar fails | Contact stays, failure is logged, user sees exactly what happened |
| Tracker write fails | Reported, while the business steps stay complete |
| Customer data contains a spreadsheet formula | Sheets values stay in RAW mode |

### Important correction

"Automatically completes the entire process" conflicts with your approval rule. **Version 1 uses one approval card per lead.** The optional later mode is in Section 13.

---

## 7. Scenario 2: personalized batch outreach

```mermaid
flowchart TD
    A["Choose Sheet or HubSpot list"] --> B["Map columns"]
    B --> C["Clean: validate, de-duplicate"]
    C --> D["Check suppression and communication status"]
    D --> E{"Eligible recipients?"}
    E -- none --> Z["Explain why and stop"]
    E -- yes --> F["Show import preview"]
    F --> G["Approve HubSpot sync if needed"]
    G --> H["Write emails from verified fields only"]
    H --> I["Code and AI quality checks"]
    I --> J["Batch review: sample, full list, exclusions"]
    J -- approved --> K["Send slowly under daily cap"]
    K --> L["Record per-recipient status"]
    L --> M["Watch same Gmail thread"]
    M --> N{"Reply type"}
    N -- unsubscribe --> O["Suppress immediately"]
    N -- interested --> P["Draft reply and update CRM"]
    N -- question --> P
    N -- "out of office" --> Q["Record, no reply"]
    N -- bounce --> R["Mark invalid"]
    P --> S["Human approves reply"]
```

### Rules

- **Batch approval:** the signature covers a manifest of every recipient and exact message. Any edit invalidates it.
- **Sending limit:** default **25 per batch and 50 per day**, configurable. This is deliberately far below Google Workspace's published 2,000 per day. Free accounts and trial accounts should stay low.
- **Throttle:** random spacing between sends, and an automatic stop after repeated failures or high bounces.
- **Compliance:** every commercial email needs truthful headers, a clear opt-out and a valid postal address. CAN-SPAM requires honoring opt-outs within 10 business days. The app will apply them immediately.
- **Lawful contact:** the user must confirm that the list is permitted. Rules differ by country.
- **No invented personalization:** the writer can use only supplied fields and past interactions.
- **Recovery:** after a crash, sending resumes only with recipients not yet marked sent. An uncertain recipient goes to `NEEDS_CHECK`.

For larger campaigns, a dedicated sender such as HubSpot sequences or marketing email is the correct upgrade. Gmail is suitable for small, personal outreach.

---

## 8. Scenario 3: Meta ad launch

```mermaid
flowchart TD
    A["Campaign brief"] --> B["Readiness check<br/>ad account, Page, Instagram, payment, terms"]
    B --> C["Read audience from Sheet or HubSpot"]
    C --> D["Remove opted-out and invalid people"]
    D --> E["Normalize and SHA-256 hash in code"]
    E --> F["AI writes copy from approved facts"]
    F --> G["User supplies or approves image or video"]
    G --> H["Policy check"]
    H --> I["Approval 1: build paused draft"]
    I --> J["Create and upload audience"]
    J --> K["Durable wait, up to 24 hours"]
    K --> L{"Audience usable?"}
    L -- no --> M["Report and stop"]
    L -- yes --> N["Create campaign PAUSED"]
    N --> O["Create ad set PAUSED<br/>Facebook and Instagram placements"]
    O --> P["Create creative"]
    P --> Q["Create ad PAUSED"]
    Q --> R["Read back IDs and generate previews"]
    R --> S["Approval 2: launch<br/>budget, dates, audience, previews"]
    S -- rejected --> T["Remains paused, no spend"]
    S -- approved --> U["Re-check, then set ACTIVE"]
    U --> V["Meta review and delivery monitoring"]
```

### Rules verified from Meta documentation

- Customer-list audiences require accepted Custom Audience terms.
- Emails and phones must be normalized, then hashed with **SHA-256**.
- Each upload can contain up to **10,000 users**, so larger lists are split.
- Audience changes usually take **up to 24 hours**.
- `special_ad_categories` is **required** for campaigns. Use an empty value or `NONE` only when the ad is not in a special category.
- Audiences suggesting health or financial information can be flagged and blocked from running.
- `PAUSED` is a valid creation status, and a paused campaign keeps child objects from delivering.

### Spend guardrails

- All objects are created `PAUSED`, and code rejects any create payload with another status.
- The launch card shows **maximum total spend**, dates, countries, placements, audience, copy, destination URL and previews.
- Launch requires a **lifetime budget or an explicit end date**, plus a workspace budget cap.
- Before activation, code re-reads the objects. Any change invalidates approval.
- Raw customer emails and phones are never sent to the AI model.
- The AI cannot activate ads, raise a budget or remove a cap.

### Demo safety

For the final project, stop at **paused campaign with previews** unless you deliberately choose to spend. The launch path can be tested with a mocked executor.

---

## 9. Connector plan

| Connector | Composio toolkit | What is needed from you | Key limits |
|---|---|---|---|
| **HubSpot** | `hubspot` | A HubSpot account | Managed app can show an unverified warning. Batch operations are limited to 100 records. HubSpot triggers need your own app |
| **Meta Ads** | `metaads` (not in the app's catalog yet) | Business portfolio, ad account, Page, payment method | Separate connection from Facebook. May need Meta App Review for outside users |
| **Facebook** | `facebook` | A Facebook **Page** | Pages only, no personal profiles |
| **Instagram** | `instagram` | **Business or Creator** account linked as Meta requires | Personal accounts are not supported |
| **Windsor.ai** | Separate MCP service | Windsor account | Use later for cross-channel reporting, read-only in this app |

**Connector order:** HubSpot, then Facebook and Instagram organic posts, then Meta Ads, then optional Windsor.

Each connector must pass one checklist:

1. Inspect live tool schemas, and do not guess arguments.
2. Request minimum scopes and show them in plain language.
3. Register capabilities and block unsafe tools.
4. Add typed payload builders and validators.
5. Add approval card details.
6. Add fake-gateway tests.
7. Run one safe live read, then one approved live write where appropriate.

---

## 10. Reliability, security and model use

| Concern | Plan |
|---|---|
| Prompt injection | External text is delimited, tool-free, schema-checked and unable to change policy |
| Tenant isolation | Identity from session. Trigger events are mapped through the stored connection, never from payload text |
| Webhook security | Verify Composio signatures, store first, process later |
| Local development | Composio delivers trigger events to a public URL. Use local polling for the demo, webhook in deployment |
| Rate limits | Per-provider limiter with backoff for safe reads only |
| Unsafe retries | Never automatically retry a write with an unknown result |
| Personal data | Minimum storage, masked logs, deletion path, no customer lists in prompts |
| Observability | Run timeline with step, agent, tool, duration, status and error |
| AI quota | Rules first, small model for classification, stronger model for writing, cached results |
| AI outage | Clear degraded mode. No write proceeds on a fallback guess |

**Model quota is a real blocker.** The current free tier allowed about 20 requests per day in testing. One 25-person outreach batch alone needs at least 25 writing calls, so these workflows require paid or higher-quota model access.

---

## 11. Target code structure

```text
logic/src/cognitive_mesh/
  capabilities/      registry, policy, risk levels, typed contracts
  connectors/        gmail, calendar, sheets, linkedin, hubspot, facebook, instagram, meta_ads
  agents/
    supervisor/      planning and clarifying
    inbox/  crm/  scheduling/  data/  content/  research/  social/  ads/
  workflows/
    engine/          runs, steps, waits, references, recovery
    playbooks/       inquiry_onboarding, batch_outreach, meta_ad_launch
    command_graph    single commands
  triggers/          event inbox, Gmail watcher, webhooks
  execution/         approval verification, executor, idempotency
  evaluation/        scenario datasets and scoring
```

The existing approval signing, tenant isolation, audit trail and at-most-once logic will be **kept and extended**, not rewritten.

---

## 12. Implementation phases

Each phase ends with tests and your review. Nothing moves forward on failing checks.

| Phase | Build | Done when |
|---|---|---|
| **0. Baseline** | Record current tests and behavior | Existing suite passes on the new branch |
| **1. Foundations** | Capability registry, split the 986-line graph, no behavior change | All existing tests still pass |
| **2. Supervisor and state** | Typed plans, active entities, safer routing | Your failed Sheets conversation works correctly |
| **3. Workflow engine** | Runs, steps, plan approval, references, waits, recovery | Crash tests prove no step runs twice |
| **4. HubSpot and CRM agent** | Search, create, safe update, notes | Live contact created once, without duplicates |
| **5. Playbook 1, manual start** | Paste or choose an email, run onboarding | Full approved run works across 4 apps |
| **6. Inbound triggers** | Gmail watcher, event inbox, classifier | Real inquiry creates one proposal, and spam is ignored |
| **7. Playbook 2** | Import, personalize, batch review, throttle, replies | 3 to 5 test recipients handled, opt-out enforced |
| **8. Facebook and Instagram** | Organic publishing | One approved post on each |
| **9. Playbook 3** | Audience, paused campaign, previews, launch gate | Paused campaign verified in Ads Manager, with no spend |
| **10. Evaluation** | About 100 commands and failure tests | Measured results ready for your professor |

### Evaluation targets

These are goals to measure, not current results:

| Metric | Target |
|---|---|
| Correct routing on realistic, typo-heavy commands | 95% or higher |
| Correct extraction of required fields | 95% or higher |
| Unsafe write without approval | **0** |
| Duplicate external write in crash tests | **0** |
| Prompt-injection test causing an unapproved action | **0** |
| Cross-workspace access | **0** |
| Ad activated without launch approval | **0** |

---

## 13. Decisions needed from you

Recommended defaults are in bold. The plan can start with them.

1. **Onboarding autonomy:** **one approval card per lead** for version 1. A later trusted mode could automatically perform only CRM notes and tracker rows, while customer email and invites still need approval.
2. **AI model access:** **enable Gemini billing** or choose another reliable paid model before Phase 5.
3. **Ads in the demo:** **stop at paused campaign with previews**, with no real spend.
4. **Windsor.ai:** **defer it**, and later use it only for reporting.
5. **Outreach limit:** **25 per batch and 50 per day.**

---

## 14. Known unknowns

These were **not** verified and will be checked at the start of the relevant phase:

- Live argument schemas for the new Composio tools. Only public tool lists were read.
- Whether `METAADS_CREATE_CUSTOM_AUDIENCE` can upload users. If not, Proxy Execute is the planned route.
- Which HubSpot, Meta and Instagram permissions your actual accounts can grant.
- Whether Meta App Review is needed for your exact demo account setup.
- HubSpot request-rate limits for your plan.
- The official sending limit for a free `@gmail.com` account. The verified figures are for Google Workspace.
- The minimum matched audience size needed before Meta delivers ads.
- Legal requirements outside the US rules checked here. This plan is engineering guidance, not legal advice.

---

## 15. Sources checked

- [Composio triggers](https://docs.composio.dev/docs/triggers) and [creating triggers](https://docs.composio.dev/docs/setting-up-triggers/creating-triggers)
- Composio toolkits: [HubSpot](https://docs.composio.dev/toolkits/hubspot), [Meta Ads](https://docs.composio.dev/toolkits/metaads), [Facebook](https://docs.composio.dev/toolkits/facebook), [Instagram](https://docs.composio.dev/toolkits/instagram), [Gmail](https://docs.composio.dev/toolkits/gmail)
- [HubSpot contacts API](https://developers.hubspot.com/docs/api-reference/latest/crm/objects/contacts/guide)
- Meta: [Customer File Custom Audiences](https://developers.facebook.com/documentation/ads-commerce/marketing-api/audiences/guides/custom-audiences), [Campaigns](https://developers.facebook.com/documentation/ads-commerce/marketing-api/reference/ad-campaign-group), [Ad Sets](https://developers.facebook.com/documentation/ads-commerce/marketing-api/reference/ad-campaign), [Special Ad Categories](https://developers.facebook.com/documentation/ads-commerce/marketing-api/audiences/special-ad-category)
- Google: [Gmail sender guidelines](https://support.google.com/mail/answer/81126), [Workspace sending limits](https://support.google.com/a/answer/166852)
- [FTC CAN-SPAM compliance guide](https://www.ftc.gov/business-guidance/resources/can-spam-act-compliance-guide-business)
- [Windsor.ai write capabilities](https://windsor.ai/write-capabilities/) and [Facebook Ads MCP](https://windsor.ai/connect/facebook-ads-mcp/)
- LangGraph: [workflows and agents](https://docs.langchain.com/oss/python/langgraph/workflows-agents), [interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts), [graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)
