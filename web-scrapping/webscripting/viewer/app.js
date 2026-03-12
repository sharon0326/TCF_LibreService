const SECTION_CONFIG = {
  oral: {
    dataset: './data/oral.json',
    eyebrow: 'Oral',
    title: 'Expression Orale',
    subtitle: 'Filter oral prompts by year, month, keyword, and existing category labels.',
    detailTitleLabel: 'Prompt',
    showAnswer: false,
    showDocuments: false,
  },
  writing1: {
    dataset: './data/writing_task1.json',
    eyebrow: 'Writing',
    title: 'Expression écrite - Tâche 1',
    subtitle: 'Browse short-message prompts and expand each row to view the model answer.',
    detailTitleLabel: 'Prompt',
    showAnswer: true,
    showDocuments: false,
  },
  writing2: {
    dataset: './data/writing_task2.json',
    eyebrow: 'Writing',
    title: 'Expression écrite - Tâche 2',
    subtitle: 'Review longer writing prompts with searchable monthly records and sample answers.',
    detailTitleLabel: 'Prompt',
    showAnswer: true,
    showDocuments: false,
  },
  writing3: {
    dataset: './data/writing_task3.json',
    eyebrow: 'Writing',
    title: 'Expression écrite - Tâche 3',
    subtitle: 'Inspect each synthesis topic together with Document 1, Document 2, and the sample answer.',
    detailTitleLabel: 'Topic',
    showAnswer: true,
    showDocuments: true,
  },
};

function getSectionKey() {
  const params = new URLSearchParams(window.location.search);
  const section = params.get('section') || 'oral';
  return SECTION_CONFIG[section] ? section : 'oral';
}

async function loadData(dataset) {
  const res = await fetch(dataset, { cache: 'no-store' });
  if (!res.ok) {
    throw new Error(`Failed to load JSON: ${res.status} ${res.statusText}`);
  }
  return await res.json();
}

function normalizeRecords(results) {
  if (!Array.isArray(results)) return [];

  return results
    .map((r) => ({
      id: typeof r.id === 'string' ? r.id : String(r.id ?? ''),
      year: typeof r.year === 'number' ? r.year : Number(r.year),
      month: typeof r.month === 'number' ? r.month : Number(r.month),
      combinaison:
        typeof r.combinaison === 'number'
          ? r.combinaison
          : Number.isFinite(Number(r.combinaison))
            ? Number(r.combinaison)
            : null,
      title: typeof r.title === 'string' ? r.title.trim() : String(r.title ?? '').trim(),
      answer: typeof r.answer === 'string' ? r.answer.trim() : String(r.answer ?? '').trim(),
      document1: typeof r.document1 === 'string' ? r.document1.trim() : String(r.document1 ?? '').trim(),
      document2: typeof r.document2 === 'string' ? r.document2.trim() : String(r.document2 ?? '').trim(),
      sourceFile: typeof r.source_file === 'string' ? r.source_file : String(r.source_file ?? ''),
      categoryPrimary:
        typeof r.category_primary === 'string' ? r.category_primary.trim() : String(r.category_primary ?? '').trim(),
      categorySecondary:
        typeof r.category_secondary === 'string'
          ? r.category_secondary.trim()
          : String(r.category_secondary ?? '').trim(),
    }))
    .filter((r) => Number.isFinite(r.year) && Number.isFinite(r.month) && r.title);
}

function uniqueSorted(values) {
  return Array.from(new Set(values)).sort((a, b) => {
    if (typeof a === 'number' && typeof b === 'number') return a - b;
    return String(a).localeCompare(String(b));
  });
}

function fillSelect(selectEl, values) {
  const current = selectEl.value;
  for (const v of values) {
    const opt = document.createElement('option');
    opt.value = String(v);
    opt.textContent = String(v);
    selectEl.appendChild(opt);
  }
  selectEl.value = current;
}

function hasCategories(records) {
  return records.some((r) => r.categoryPrimary);
}

function applyFilters(records, { year, month, category, q }) {
  const yearNum = year ? Number(year) : null;
  const monthNum = month ? Number(month) : null;
  const query = (q || '').trim().toLowerCase();

  return records.filter((r) => {
    if (yearNum !== null && r.year !== yearNum) return false;
    if (monthNum !== null && r.month !== monthNum) return false;
    if (category && r.categoryPrimary !== category) return false;
    if (query && !r.title.toLowerCase().includes(query)) return false;
    return true;
  });
}

function updateStatus(statusEl, total, shown) {
  statusEl.textContent = `Showing ${shown} / ${total}`;
}

function appendDetailBlock(parent, label, text) {
  if (!text) return;
  const block = document.createElement('section');
  block.className = 'detailBlock';

  const heading = document.createElement('h3');
  heading.className = 'detailBlock__title';
  heading.textContent = label;

  const content = document.createElement('p');
  content.className = 'detailBlock__content';
  content.textContent = text;

  block.appendChild(heading);
  block.appendChild(content);
  parent.appendChild(block);
}

function createDetailPanel(record, config) {
  const wrapper = document.createElement('div');
  wrapper.className = 'detailPanel';

  const meta = document.createElement('div');
  meta.className = 'detailMeta';
  meta.textContent = `Source: ${record.sourceFile || 'Unknown'}${record.combinaison !== null ? ` · Combinaison ${record.combinaison}` : ''}`;
  wrapper.appendChild(meta);

  appendDetailBlock(wrapper, config.detailTitleLabel, record.title);

  if (record.categoryPrimary || record.categorySecondary) {
    appendDetailBlock(
      wrapper,
      'Category',
      [record.categoryPrimary, record.categorySecondary].filter(Boolean).join(' / ')
    );
  }

  if (config.showDocuments) {
    appendDetailBlock(wrapper, 'Document 1', record.document1);
    appendDetailBlock(wrapper, 'Document 2', record.document2);
  }

  if (config.showAnswer) {
    appendDetailBlock(wrapper, 'Sample answer', record.answer);
  }

  return wrapper;
}

function renderEmptyState(tbody, columnCount) {
  const tr = document.createElement('tr');
  const td = document.createElement('td');
  td.colSpan = columnCount;
  td.className = 'emptyCell';
  td.textContent = 'No records match the current filters.';
  tr.appendChild(td);
  tbody.replaceChildren(tr);
}

function renderTable(tbody, records, config, showCategory) {
  const frag = document.createDocumentFragment();
  const columnCount = showCategory ? 6 : 5;

  if (!records.length) {
    renderEmptyState(tbody, columnCount);
    return;
  }

  for (const record of records) {
    const row = document.createElement('tr');
    row.className = 'dataRow';

    const yearCell = document.createElement('td');
    yearCell.textContent = String(record.year);

    const monthCell = document.createElement('td');
    monthCell.textContent = String(record.month);

    const combinaisonCell = document.createElement('td');
    combinaisonCell.textContent = record.combinaison !== null ? String(record.combinaison) : '—';

    const titleCell = document.createElement('td');
    titleCell.textContent = record.title;

    row.appendChild(yearCell);
    row.appendChild(monthCell);
    row.appendChild(combinaisonCell);
    row.appendChild(titleCell);

    if (showCategory) {
      const categoryCell = document.createElement('td');
      categoryCell.textContent = record.categoryPrimary || '—';
      row.appendChild(categoryCell);
    }

    const actionCell = document.createElement('td');
    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'detailButton';
    toggle.textContent = 'View';
    actionCell.appendChild(toggle);
    row.appendChild(actionCell);

    const detailRow = document.createElement('tr');
    detailRow.className = 'detailRow is-hidden';

    const detailCell = document.createElement('td');
    detailCell.colSpan = columnCount;
    detailCell.appendChild(createDetailPanel(record, config));
    detailRow.appendChild(detailCell);

    toggle.addEventListener('click', () => {
      const isHidden = detailRow.classList.toggle('is-hidden');
      toggle.textContent = isHidden ? 'View' : 'Hide';
    });

    frag.appendChild(row);
    frag.appendChild(detailRow);
  }

  tbody.replaceChildren(frag);
}

function setSectionHeader(config) {
  const eyebrow = document.getElementById('sectionEyebrow');
  const title = document.getElementById('sectionTitle');
  const subtitle = document.getElementById('sectionSubtitle');

  eyebrow.textContent = config.eyebrow;
  title.textContent = config.title;
  subtitle.textContent = config.subtitle;
  document.title = `${config.title} · TCF Question Bank Viewer`;
}

function toggleCategoryUI(showCategory) {
  const categoryControl = document.getElementById('categoryControl');
  const categoryHeader = document.getElementById('categoryHeader');
  categoryControl.classList.toggle('is-hidden', !showCategory);
  categoryHeader.classList.toggle('is-hidden', !showCategory);
}

async function main() {
  if (!document.getElementById('tbody')) {
    return;
  }

  const sectionKey = getSectionKey();
  const config = SECTION_CONFIG[sectionKey];
  setSectionHeader(config);

  const statusEl = document.getElementById('status');
  const tbody = document.getElementById('tbody');
  const yearFilter = document.getElementById('yearFilter');
  const monthFilter = document.getElementById('monthFilter');
  const categoryFilter = document.getElementById('categoryFilter');
  const titleFilter = document.getElementById('titleFilter');
  const resetBtn = document.getElementById('resetBtn');

  let records = [];
  let showCategory = false;

  try {
    const json = await loadData(config.dataset);
    records = normalizeRecords(json.results);
    showCategory = hasCategories(records);
    toggleCategoryUI(showCategory);

    const years = uniqueSorted(records.map((r) => r.year));
    fillSelect(yearFilter, years);

    if (showCategory) {
      const categories = uniqueSorted(records.map((r) => r.categoryPrimary).filter(Boolean));
      fillSelect(categoryFilter, categories);
    }

    const rerender = () => {
      const filtered = applyFilters(records, {
        year: yearFilter.value,
        month: monthFilter.value,
        category: showCategory ? categoryFilter.value : '',
        q: titleFilter.value,
      });
      renderTable(tbody, filtered, config, showCategory);
      updateStatus(statusEl, records.length, filtered.length);
    };

    const onChange = () => rerender();

    yearFilter.addEventListener('change', onChange);
    monthFilter.addEventListener('change', onChange);
    categoryFilter.addEventListener('change', onChange);
    titleFilter.addEventListener('input', onChange);

    resetBtn.addEventListener('click', () => {
      yearFilter.value = '';
      monthFilter.value = '';
      categoryFilter.value = '';
      titleFilter.value = '';
      rerender();
    });

    rerender();
  } catch (err) {
    statusEl.textContent = String(err && err.message ? err.message : err);
    tbody.replaceChildren();
  }
}

main();
