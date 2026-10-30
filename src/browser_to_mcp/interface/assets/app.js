'use strict';
const $ = selector => document.querySelector(selector);
let token = location.hash.slice(1) || sessionStorage.getItem('local-token') || '';
if (token) sessionStorage.setItem('local-token', token);
history.replaceState(null, '', location.pathname);
const importTargets = new Map();
let snapshot, review, selectedTool, lastState = '',
  polling = false;
const message = (text, error = false) => {
  $('#message').textContent = text;
  $('#message').classList.toggle('error', error);
};
async function api(operation, args = {}) {
  const response = await fetch('/api', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: 'Bearer ' + token
    },
    body: JSON.stringify({
      operation,
      arguments: args
    })
  });
  const value = await response.json();
  if (!response.ok) throw Error(value.error || 'Operation refused.');
  return value;
}
const show = name => {
  for (const section of document.querySelectorAll('main > section')) section.hidden = section
    .id !== name;
  for (const button of document.querySelectorAll('[data-view]')) {
    if (button.dataset.view === name) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  }
};

function button(text, action, parent) {
  const el = document.createElement('button');
  el.type = 'button';
  el.textContent = text;
  el.addEventListener('click', () => safely(action));
  parent.append(el);
  return el;
}

function pre(value, parent) {
  const el = document.createElement('pre');
  el.textContent = JSON.stringify(value, null, 2);
  parent.append(el);
}

function card(title, parent) {
  const el = document.createElement('article');
  const heading = document.createElement('h3');
  heading.textContent = title;
  el.append(heading);
  parent.append(el);
  return el;
}
async function safely(action) {
  try {
    await action();
  } catch (error) {
    message(error.message, true);
  }
}
async function loadReview(tool, version) {
  review = await api('review', {
    tool_id: tool,
    version
  });
  $('#review-tool').value = tool;
  $('#review-version').value = version;
  $('#review-approved').checked = false;
  $('#plan-approved').checked = false;
  $('#review-data').replaceChildren();
  pre(review, $('#review-data'));
  $('#evaluation-form button').disabled = review.version.state !== 'draft';
  $('#publish-form button').disabled = !['tested', 'published'].includes(review.version.state);
  if (review.approval && review.tool.live !== version) button('Restore this approved version',
    async () => {
      await api('rollback', {
        tool_id: tool,
        version,
        revision: review.tool.revision,
        candidate_sha256: review.version.sha
      });
      message('Approved version restored. Restart external servers to adopt it.');
      show('tools');
    }, $('#review-data'));
  show('review');
}
async function poll() {
  if (polling) return;
  polling = true;
  try {
    snapshot = await api('status');
    $('#worker').textContent = snapshot.worker_ready ?
      'Worker ready. Client-led creation uses your connected assistant; provider mode needs explicit worker configuration.' :
      'Worker unavailable. Check for another worker or restart the interface after correcting configuration.';
    const state = JSON.stringify(snapshot);
    if (state === lastState) return;
    lastState = state;
    const focusKey = document.activeElement?.closest('article')?.dataset.key;
    const focusText = document.activeElement?.textContent;
    $('#tool-list').replaceChildren();
    $('#job-list').replaceChildren();
    $('#run-list').replaceChildren();
    for (const tool of snapshot.tools) {
      const el = card(tool.display_name, $('#tool-list'));
      el.dataset.key = tool.id;
      pre({
        id: tool.id,
        name: tool.name,
        published_version: tool.live,
        latest_version: tool.latest
      }, el);
      const actions = document.createElement('div');
      actions.className = 'actions';
      el.append(actions);
      button('Review latest', () => loadReview(tool.id, tool.latest), actions);
      if (tool.live !== null) {
        button('Run tool', async () => {
          selectedTool = tool.id;
          const r = await api('review', {
            tool_id: tool.id,
            version: tool.live
          });
          $('#run-form').hidden = false;
          $('#run-title').textContent = 'Run ' + tool.display_name;
          const values = {};
          for (const [name, input] of Object.entries(r.workflow.inputs)) {
            if (!input.required) continue;
            values[name] = input.type === 'string' ? '' : input.type === 'boolean' ? false : 0;
          }
          $('#run-inputs').value = JSON.stringify(values, null, 2);
          pre(r.workflow.inputs, $('#run-form'));
          $('#run-inputs').focus();
        }, actions);
        button('Retire', async () => {
          await api('retire', {
            tool_id: tool.id,
            revision: tool.revision
          });
          message(
            'Retired. Restart external library servers to stop them adopting the old snapshot.'
          );
          await poll();
        }, actions);
      }
      button('Version history', async () => {
        const events = await api('history', {
          tool_id: tool.id
        });
        pre(events, el);
        const versions = [...new Set(events.map(e => e.version).filter(Boolean))];
        for (const version of versions) button('Review version ' + version, () =>
          loadReview(tool.id, version), el);
      }, actions);
    }
    if (!snapshot.tools.length) $('#tool-list').textContent =
      'No tools yet. Start a new task to create your first draft.';
    for (const job of snapshot.jobs) {
      const el = card(job.id + ' · ' + job.state, $('#job-list'));
      el.dataset.key = job.id;
      pre(job, el);
      if (job.question) {
        const question = document.createElement('p');
        question.textContent = job.question.prompt;
        el.append(question);
        for (const choice of job.question.choices) button(choice, async () => {
          await api('answer', {
            job_id: job.id,
            question_id: job.question.id,
            revision: job.revision,
            value: choice
          });
          message('Answer saved.');
        }, el);
        if (job.question.kind === 'draft_proposal') {
          const hint = document.createElement('p');
          hint.textContent =
            'Waiting for your connected assistant to supply a proposal through MCP. No worker model call is required. See MCP setup.';
          el.append(hint);
          button('Inspect assistant context', async () => pre(await api('context', {
            job_id: job.id
          }), el), el);
        }
      }
      if (!['failed', 'cancelled', 'completed'].includes(job.state)) button('Cancel build',
        async () => {
          await api('cancel', {
            job_id: job.id
          });
          message('Cancellation requested.');
        }, el);
      if (['ready_for_review', 'completed'].includes(job.state)) {
        button('Inspect draft & schemas', async () => {
          const draft = await api('draft', {
            job_id: job.id
          });
          const parent = $('#draft-view');
          parent.replaceChildren();
          pre(draft, parent);
          const form = document.createElement('form');
          const label = document.createElement('label');
          label.textContent = 'Preview inputs (JSON)';
          const input = document.createElement('textarea');
          input.rows = 5;
          input.value = '{}';
          label.append(input);
          form.append(label);
          const submit = document.createElement('button');
          submit.textContent = 'Run this draft preview';
          form.append(submit);
          parent.append(form);
          form.addEventListener('submit', event => {
            event.preventDefault();
            safely(async () => {
              await api('preview', {
                job_id: job.id,
                candidate_sha256: draft.candidate_sha256,
                inputs: JSON.parse(input.value),
                request_key: crypto.randomUUID()
              });
              show('runs');
              message(
                'Draft preview started. This does not publish or independently test it.'
              );
            });
          });
        }, el);
      }
      if (job.state === 'completed') {
        const label = document.createElement('label');
        label.textContent = 'Save draft to';
        const target = document.createElement('select');
        target.setAttribute('aria-label', 'Save draft to');
        const fresh = document.createElement('option');
        fresh.value = '';
        fresh.textContent = 'New tool';
        target.append(fresh);
        for (const tool of snapshot.tools) {
          const option = document.createElement('option');
          option.value = tool.id;
          option.textContent = 'New version of ' + tool.name;
          target.append(option);
        }
        target.value = importTargets.get(job.id) || '';
        target.addEventListener('change', () => importTargets.set(job.id, target.value));
        label.append(target);
        el.append(label);
        button('Keep in library for independent tests', async () => {
          const r = await api('import', {
            job_id: job.id,
            ...(target.value ? {
              tool_id: target.value
            } : {})
          });
          await loadReview(r.tool.id, r.version.version);
          message('Imported as a draft. Tests and approval are still required.');
        }, el);
      }
      if (['failed', 'cancelled'].includes(job.state)) button('Correct & start a new task',
        () => {
          show('new');
          $('#task-form input').focus();
          message(
            'Correct the task fields and explicitly start a new attempt. The old attempt is preserved.'
          );
        }, el);
    }
    for (const op of snapshot.operations) {
      const el = card(op.kind + ' · ' + op.state, $('#run-list'));
      el.dataset.key = op.id;
      pre(op, el);
      if (['queued', 'running'].includes(op.state)) button('Cancel operation', async () => {
        await api('cancel-operation', {
          operation_id: op.id
        });
        message('Cancellation requested.');
      }, el);
      if (op.result?.review) button('Review tested version', () => loadReview(op.result.review
        .tool.id, op.result.review.version.version), el);
    }
    if (focusKey) {
      const article = [...document.querySelectorAll('article')].find(el => el.dataset.key ===
        focusKey);
      const target = article && [...article.querySelectorAll('button')].find(el => el
        .textContent === focusText);
      target?.focus({
        preventScroll: true
      });
    }
    const command = directory => ({
      command: snapshot.executable,
      args: directory
    });
    const config = {
      mcpServers: {
        build_jobs: command(['jobs', '--directory', snapshot.jobs_directory, 'server']),
        published_tools: command(['library', '--directory', snapshot.library_directory,
          'server'
        ])
      }
    };
    $('#mcp-config').textContent = JSON.stringify(config, null, 2);
  } catch (error) {
    message(error.message, true);
  } finally {
    polling = false;
  }
}

function row(kind) {
  const el = document.createElement('div');
  el.className = 'row';
  for (const [field, title] of [
      ['name', 'Name'],
      ['description', 'Description'], ...(kind === 'input' ? [
        ['example', 'Example value']
      ] : [
        ['link_path_prefix', 'Link path prefix (optional)']
      ])
    ]) {
    const label = document.createElement('label');
    label.textContent = title;
    const input = document.createElement('input');
    input.dataset.field = field;
    input.required = field !== 'link_path_prefix';
    label.append(input);
    el.append(label);
  }
  const label = document.createElement('label');
  label.textContent = 'Type';
  const select = document.createElement('select');
  select.dataset.field = 'type';
  select.setAttribute('aria-label', 'Type');
  for (const type of ['string', 'integer', 'number', 'boolean']) {
    const option = document.createElement('option');
    option.textContent = type;
    select.append(option);
  }
  label.append(select);
  el.append(label);
  button('Remove', () => el.remove(), el);
  $(kind === 'input' ? '#input-rows' : '#field-rows').append(el);
}

function rows(selector, example) {
  const result = {};
  for (const el of document.querySelectorAll(selector + ' .row')) {
    const value = {};
    for (const input of el.querySelectorAll('[data-field]')) value[input.dataset.field] = input
      .value;
    if (Object.hasOwn(result, value.name)) throw Error('Field names must be unique.');
    const name = value.name;
    delete value.name;
    if (!value.link_path_prefix) delete value.link_path_prefix;
    if (example && value.type !== 'string') value.example = JSON.parse(value.example);
    result[name] = value;
  }
  return result;
}
for (const el of document.querySelectorAll('[data-view]')) el.addEventListener('click', () => show(
  el.dataset.view));
$('#add-input').addEventListener('click', () => row('input'));
$('#add-field').addEventListener('click', () => row('field'));
row('field');
$('#task-form').addEventListener('submit', event => {
  event.preventDefault();
  safely(async () => {
    const f = new FormData(event.target),
      mode = f.get('generation');
    const task = {
      name: f.get('name'),
      start_url: f.get('start_url'),
      description: f.get('description'),
      allowed_domains: f.get('domains').split(',').map(s => s.trim()).filter(Boolean),
      inputs: rows('#input-rows', true),
      fields: rows('#field-rows', false),
      shape: f.get('shape') || null,
      unique_by: f.get('unique_by') || null,
      max_items: Number(f.get('max_items')),
      max_pages: Number(f.get('max_pages')),
      allow_empty: f.has('allow_empty')
    };
    if (f.has('caller_page_limit')) task.max_pages_input = 'max_pages';
    const budgets = {};
    for (const key of ['browser_actions', 'model_requests', 'tokens', 'cost_microusd'])
      budgets[key] = Number(f.get(key));
    if (mode === 'provider' && !snapshot?.provider_ready) throw Error(
      'Provider mode is not configured in the trusted worker environment. Restart after configuring it, or use your connected assistant.'
    );
    const start = $('#start-task');
    start.disabled = true;
    try {
      const job = await api('start', {
        task,
        generation: mode,
        budgets,
        request_key: crypto.randomUUID()
      });
      show('jobs');
      message('Build started: ' + job.id);
      await poll();
    } finally {
      start.disabled = false;
    }
  });
});
$('#review-form').addEventListener('submit', event => {
  event.preventDefault();
  safely(() => loadReview($('#review-tool').value, Number($('#review-version').value)));
});
$('#evaluation-form').addEventListener('submit', event => {
  event.preventDefault();
  safely(async () => {
    if (!review) throw Error('Load a version to review first.');
    const result = await api('evaluate', {
      tool_id: review.tool.id,
      version: review.version.version,
      suite: JSON.parse($('#suite').value),
      approved_plan: $('#plan-approved').checked,
      request_key: crypto.randomUUID()
    });
    message('Tests started: ' + result.operation_id);
    show('runs');
  });
});
$('#publish-form').addEventListener('submit', event => {
  event.preventDefault();
  safely(async () => {
    if (!review) throw Error('Load the complete current review first.');
    await api('publish', {
      tool_id: review.tool.id,
      version: review.version.version,
      revision: review.tool.revision,
      candidate_sha256: review.version.sha,
      approve: $('#review-approved').checked
    });
    message('Published. Restart external library servers to adopt this version.');
    show('tools');
    await poll();
  });
});
$('#run-form').addEventListener('submit', event => {
  event.preventDefault();
  safely(async () => {
    const result = await api('run', {
      tool_id: selectedTool,
      inputs: JSON.parse($('#run-inputs').value),
      request_key: crypto.randomUUID()
    });
    message('Run started: ' + result.operation_id);
    show('runs');
  });
});
$('#copy-config').addEventListener('click', () => safely(async () => {
  await navigator.clipboard.writeText($('#mcp-config').textContent);
  message('MCP configuration copied.');
}));
show('tools');
void poll();
setInterval(poll, 2000);
