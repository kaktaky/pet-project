(() => {
    const dialog = document.getElementById('suggestion-dialog');
    if (!dialog) return;
    const panel = document.getElementById('suggestion-panel');
    const status = document.getElementById('suggestion-status');
    const next = document.getElementById('suggestion-next');
    const eat = document.getElementById('suggestion-eat');
    let variants = [], index = 0, remaining = 0, day = '', busy = false, requestId;
    function newRequestId() {
        if (typeof crypto.randomUUID === 'function') return crypto.randomUUID();
        // HTTP в домашней сети: randomUUID доступен только в secure context.
        const bytes = crypto.getRandomValues(new Uint8Array(16));
        bytes[6] = (bytes[6] & 15) | 64;
        bytes[8] = (bytes[8] & 63) | 128;
        const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
        return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
    }
    const amount = food => Object.fromEntries(['cal', 'p', 'f', 'c'].map(key => [key, Math.round((food[key] || 0) * food.portions)]));
    const totals = () => variants[index].items.reduce((sum, food) => {
        const values = amount(food);
        Object.keys(sum).forEach(key => sum[key] += values[key]);
        return sum;
    }, {cal: 0, p: 0, f: 0, c: 0});
    function update() {
        const total = totals();
        document.getElementById('suggestion-cal').textContent = total.cal;
        document.getElementById('suggestion-macros').textContent = `Б ${total.p} г · Ж ${total.f} г · У ${total.c} г`;
        const after = remaining - total.cal;
        status.textContent = after >= 0 ? `После порции останется ${after} ккал` : `Выше дневной цели на ${-after} ккал`;
        status.className = `text-xs font-semibold mt-3 ${after >= 0 ? 'text-emerald-500' : 'text-amber-500'}`;
    }
    function render() {
        panel.replaceChildren();
        const variant = variants[index];
        if (!variant) return;
        requestId = newRequestId();
        const badge = document.createElement('p');
        badge.className = 'discovery-label mb-4';
        badge.textContent = `Вариант ${index + 1} из ${variants.length} · ${variant.items.length === 1 ? 'одно блюдо' : 'сочетание'}`;
        panel.appendChild(badge);
        variant.items.forEach(food => {
            const card = document.createElement('div');
            card.className = 'suggestion-card p-4 mb-3 rounded-2xl bg-slate-50 dark:bg-slate-800';
            const title = document.createElement('h3');
            title.className = 'text-lg font-black break-words';
            title.textContent = food.name;
            const base = document.createElement('p');
            base.className = 'text-xs text-slate-500 mt-1';
            base.textContent = `1 порция = ${food.cal} ккал · Б ${food.p} Ж ${food.f} У ${food.c}`;
            const label = document.createElement('label');
            label.className = 'block text-xs font-bold mt-4';
            const text = document.createElement('span');
            const range = document.createElement('input');
            range.type = 'range'; range.min = '.25'; range.max = '3'; range.step = '.25';
            range.value = food.portions; range.className = 'portion-range';
            range.setAttribute('aria-label', `Порции: ${food.name}`);
            const refresh = () => {
                text.textContent = `Количество порций: ${food.portions.toLocaleString('ru-RU')}`;
            };
            refresh();
            range.addEventListener('input', () => {
                food.portions = Number(range.value);
                requestId = newRequestId();
                refresh(); update();
            });
            label.append(text, range); card.append(title, base, label); panel.appendChild(card);
        });
        update();
        eat.hidden = false; next.hidden = variants.length < 2;
    }
    document.getElementById('suggestion-open').addEventListener('click', async () => {
        dialog.showModal(); panel.textContent = 'Подбираем блюда…'; status.textContent = '';
        document.getElementById('suggestion-cal').textContent = '—';
        document.getElementById('suggestion-macros').textContent = '';
        eat.hidden = true; next.hidden = true;
        try {
            const response = await fetch('/api/suggestions');
            if (!response.ok) throw new Error('Не удалось загрузить блюда. Попробуй ещё раз.');
            const data = await response.json();
            variants = data.variants; remaining = data.remaining; day = data.day; index = 0;
            if (!variants.length) {
                panel.textContent = remaining <= 0 ? 'Дневная цель уже достигнута. Можно добавить еду обычной кнопкой «+».' : 'Пока нет подходящих порций. Добавь в избранное ещё несколько блюд с калориями.';
                document.getElementById('suggestion-cal').textContent = '—';
                document.getElementById('suggestion-macros').textContent = '';
            } else render();
        } catch (error) { panel.textContent = error.message; }
    });
    document.getElementById('suggestion-close').addEventListener('click', () => { if (!busy) dialog.close(); });
    dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
    dialog.addEventListener('click', event => {
        const bounds = dialog.getBoundingClientRect();
        if (!busy && event.target === dialog && (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom)) dialog.close();
    });
    next.addEventListener('click', () => { if (!busy) { index = (index + 1) % variants.length; render(); } });
    eat.addEventListener('click', async () => {
        if (busy || !variants[index]) return;
        busy = true; eat.disabled = true; next.disabled = true;
        dialog.querySelectorAll('input[type=range]').forEach(input => input.disabled = true);
        eat.textContent = 'Сохраняем…';
        try {
            const response = await fetch('/api/suggestions/eat', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({day, request_id: requestId, items: variants[index].items.map(food => ({id: food.id, portions: food.portions}))})
            });
            if (!response.ok) {
                const error = await response.json();
                throw new Error(typeof error.detail === 'string' ? error.detail : 'Проверь выбранные порции и попробуй ещё раз.');
            }
            window.location.assign('/?added=1');
        } catch (error) {
            status.textContent = error.message; status.className = 'text-xs text-red-500 mt-3';
            busy = false; eat.disabled = false; next.disabled = false; eat.textContent = 'Я это съел';
            dialog.querySelectorAll('input[type=range]').forEach(input => input.disabled = false);
        }
    });
})();
