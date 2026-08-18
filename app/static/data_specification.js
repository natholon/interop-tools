// A small, self-contained script for the Data Specification page - kept
// separate from app.js (not sharing its scope, since this app has no
// module system) rather than gated additions inside app.js's own
// DOMContentLoaded listener, since this page's own panes never need to be
// added to index.html's show*() hidden-toggle chains or vice versa - see
// CLAUDE.md's own Data Specification section for why a genuinely separate
// page (not a new pane on index.html) was chosen specifically to sidestep
// that recurring bug class.
//
// escapeHtml/highlightJsonFragment below are the identical escaping/
// token-coloring logic app.js::escapeHtml/highlightJson already use -
// duplicated, not imported (no module system), and highlightJsonFragment
// is deliberately usable on a *partial* JSON fragment (a gap between two
// marks, not necessarily a complete, independently-valid JSON snippet) -
// the regex is token-based, not a real JSON parser, so it degrades
// gracefully (falls through to plain escaped text) at a fragment boundary
// that happens to split a token, rather than misrendering.

function escapeHtml(str) {
    return str.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

function highlightJsonFragment(fragment) {
    const escaped = escapeHtml(fragment);
    return escaped.replace(
        /("(\\u[a-fA-F0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)/g,
        (match) => {
            let cls = "json-number";
            if (/^"/.test(match)) {
                cls = /:$/.test(match) ? "json-key" : "json-string";
            } else if (/^(true|false|null)$/.test(match)) {
                cls = "json-literal";
            }
            return `<span class="${cls}">${match}</span>`;
        }
    );
}

// Builds the HTML for one pane: `text` interleaved with `<mark>`-wrapped
// spans at the given (start, end) offsets. `spans` must be pre-sorted by
// start and non-overlapping (guaranteed by construction - see
// buildSourceSpans/buildFhirSpans below, which only ever draw from
// app/provenance/highlighting.py's own non-overlapping, per-leaf-field
// spans) - a defensively out-of-order/overlapping span is simply skipped
// rather than risking corrupted markup.
function renderWithMarks(text, spans, { syntaxHighlightGaps = false } = {}) {
    let html = "";
    let cursor = 0;
    for (const span of spans) {
        if (span.start < cursor || span.end <= span.start || span.end > text.length) continue;
        const gap = text.slice(cursor, span.start);
        html += syntaxHighlightGaps ? highlightJsonFragment(gap) : escapeHtml(gap);
        const inner = escapeHtml(text.slice(span.start, span.end));
        const classes = ["xwalk-mark"];
        if (span.tokenType) classes.push(`json-${span.tokenType}`);
        const style = span.colorIndex != null ? ` style="--match-color: var(--match-${span.colorIndex})"` : "";
        html += `<mark class="${classes.join(" ")}" data-match="${span.id}"${style}>${inner}</mark>`;
        cursor = span.end;
    }
    html += syntaxHighlightGaps ? highlightJsonFragment(text.slice(cursor)) : escapeHtml(text.slice(cursor));
    return html;
}

function buildSourceSpans(matches) {
    const spans = [];
    matches.forEach((m, i) => {
        if (m.source_span) spans.push({ start: m.source_span[0], end: m.source_span[1], id: i, colorIndex: m.color_index });
    });
    spans.sort((a, b) => a.start - b.start);
    return spans;
}

function buildFhirSpans(matches) {
    const spans = [];
    matches.forEach((m, i) => {
        if (m.fhir_span) {
            spans.push({ start: m.fhir_span[0], end: m.fhir_span[1], id: i, colorIndex: m.color_index, tokenType: m.fhir_token_type });
        }
    });
    spans.sort((a, b) => a.start - b.start);
    return spans;
}

document.addEventListener("DOMContentLoaded", () => {
    const form = document.getElementById("crosswalk-form");
    const textarea = document.getElementById("hl7_text");
    const fileInput = document.getElementById("hl7_file");
    const generateBtn = document.getElementById("crosswalk-generate-sample");
    const messageTypeSelect = document.getElementById("message-type-select");
    const errorPane = document.getElementById("crosswalk-error-pane");
    const outputPane = document.getElementById("crosswalk-output-pane");
    const dedupCheckbox = document.getElementById("crosswalk-deduplicate");
    const dedupSummaryEl = document.getElementById("crosswalk-dedup-summary");
    const unsupportedBanner = document.getElementById("crosswalk-unsupported-banner");
    const tableWrapper = document.getElementById("crosswalk-table-wrapper");
    const tableBody = document.getElementById("crosswalk-table-body");
    const rawJson = document.getElementById("crosswalk-raw-json");
    const toast = document.getElementById("toast");

    const sourcePre = document.getElementById("source-highlighted");
    const sourceCode = document.getElementById("source-highlighted-code");
    const editSourceBtn = document.getElementById("edit-source-btn");
    const fhirPlaceholder = document.getElementById("fhir-placeholder");
    const fhirPre = document.getElementById("fhir-highlighted");
    const fhirCode = document.getElementById("fhir-highlighted-code");
    const tooltip = document.getElementById("xwalk-tooltip");

    let currentReportEntries = [];

    let toastTimer;
    function showToast(message) {
        if (!toast) return;
        toast.textContent = message;
        toast.hidden = false;
        clearTimeout(toastTimer);
        toastTimer = setTimeout(() => {
            toast.hidden = true;
        }, 2200);
    }

    function setBusy(button, busy) {
        if (!button) return;
        const label = button.querySelector(".btn-label");
        if (busy) {
            if (label) {
                button.dataset.originalLabel = label.textContent;
                label.textContent = button.dataset.busyText || "Working…";
            }
            button.classList.add("is-busy");
            button.disabled = true;
        } else {
            if (label && button.dataset.originalLabel) {
                label.textContent = button.dataset.originalLabel;
            }
            button.classList.remove("is-busy");
            button.disabled = false;
        }
    }

    function showError(category, message) {
        if (!errorPane) return;
        errorPane.innerHTML = "";
        const strong = document.createElement("strong");
        strong.textContent = category;
        const pre = document.createElement("pre");
        pre.textContent = message;
        errorPane.append(strong, pre);
        errorPane.hidden = false;
        if (outputPane) outputPane.hidden = true;
    }

    function showEditableSource() {
        if (textarea) textarea.hidden = false;
        if (sourcePre) sourcePre.hidden = true;
        if (editSourceBtn) editSourceBtn.hidden = true;
    }

    function showHighlightedSource() {
        if (textarea) textarea.hidden = true;
        if (sourcePre) sourcePre.hidden = false;
        if (editSourceBtn) editSourceBtn.hidden = false;
    }

    if (editSourceBtn) {
        editSourceBtn.addEventListener("click", () => {
            showEditableSource();
            if (textarea) textarea.focus();
        });
    }

    function renderCrosswalkTable(entries) {
        if (!tableBody) return;
        tableBody.innerHTML = "";
        for (const entry of entries) {
            const tr = document.createElement("tr");
            if (entry.derivation === "inferred") tr.className = "crosswalk-inferred";

            const locationCell = document.createElement("td");
            if (entry.derivation === "inferred") {
                const em = document.createElement("em");
                em.textContent = entry.reason || "(inferred)";
                locationCell.appendChild(em);
            } else {
                locationCell.textContent = entry.source_location || "";
            }

            const fieldNameCell = document.createElement("td");
            fieldNameCell.textContent = entry.field_label || "";

            const pathCell = document.createElement("td");
            pathCell.textContent = entry.fhir_path;

            const sourceValueCell = document.createElement("td");
            sourceValueCell.textContent = entry.source_value ?? "";

            const valueCell = document.createElement("td");
            valueCell.textContent = entry.value ?? "";

            tr.append(locationCell, fieldNameCell, pathCell, sourceValueCell, valueCell);
            tableBody.appendChild(tr);
        }
        if (tableWrapper) tableWrapper.hidden = false;
        if (rawJson) rawJson.hidden = true;
    }

    function showCrosswalk(report, highlighting, dedupSummary) {
        if (!outputPane) return;
        currentReportEntries = report.entries || [];

        if (dedupSummaryEl) {
            if (dedupSummary) {
                const count = dedupSummary.resources_merged || 0;
                dedupSummaryEl.textContent = count
                    ? `Merged ${count} duplicate resource${count === 1 ? "" : "s"}.`
                    : "No duplicate resources found.";
                dedupSummaryEl.hidden = false;
            } else {
                dedupSummaryEl.hidden = true;
            }
        }

        if (unsupportedBanner) {
            if (report.unsupported) {
                unsupportedBanner.textContent = report.unsupported_reason || "Field-level provenance isn't implemented yet for this input.";
                unsupportedBanner.hidden = false;
            } else {
                unsupportedBanner.hidden = true;
            }
        }

        renderCrosswalkTable(currentReportEntries);

        if (highlighting && sourceCode && fhirCode) {
            const sourceSpans = buildSourceSpans(highlighting.matches || []);
            const fhirSpans = buildFhirSpans(highlighting.matches || []);
            sourceCode.innerHTML = renderWithMarks(highlighting.display_source_text, sourceSpans);
            fhirCode.innerHTML = renderWithMarks(highlighting.fhir_json_text, fhirSpans, { syntaxHighlightGaps: true });
            showHighlightedSource();
            if (fhirPlaceholder) fhirPlaceholder.hidden = true;
            if (fhirPre) fhirPre.hidden = false;
        }

        outputPane.hidden = false;
        if (errorPane) errorPane.hidden = true;
    }

    // ---- hover correlation: mark <-> mark across both panes, plus a
    // floating tooltip with the fact's own detail (pulled from the
    // already-fetched report.entries, matched by array position - the
    // same order highlighting.matches was built in). ----

    function findEntryForMatchId(matchId) {
        return currentReportEntries[Number(matchId)];
    }

    function setActive(matchId, active) {
        document.querySelectorAll(`.xwalk-mark[data-match="${matchId}"]`).forEach((el) => {
            el.classList.toggle("is-active", active);
        });
    }

    function positionTooltip(x, y) {
        if (!tooltip) return;
        const margin = 12;
        const rect = tooltip.getBoundingClientRect();
        let left = x + margin;
        let top = y + margin;
        if (left + rect.width > window.innerWidth) left = x - rect.width - margin;
        if (top + rect.height > window.innerHeight) top = y - rect.height - margin;
        tooltip.style.left = `${Math.max(4, left)}px`;
        tooltip.style.top = `${Math.max(4, top)}px`;
    }

    function showTooltip(matchId, x, y) {
        if (!tooltip) return;
        const entry = findEntryForMatchId(matchId);
        if (!entry) return;
        const rows = [];
        if (entry.derivation === "inferred") {
            rows.push(["Inferred", entry.reason || "(no source field)"]);
        } else {
            rows.push(["Source Location", entry.source_location || ""]);
            if (entry.field_label) rows.push(["Source Field Name", entry.field_label]);
        }
        if (entry.source_value != null) rows.push(["Source Value", entry.source_value]);
        rows.push(["FHIR Path", entry.fhir_path]);
        if (entry.value != null) rows.push(["FHIR Value", entry.value]);

        const dl = document.createElement("dl");
        dl.style.margin = "0";
        for (const [label, value] of rows) {
            const dt = document.createElement("dt");
            dt.textContent = label;
            const dd = document.createElement("dd");
            dd.textContent = value;
            dl.append(dt, dd);
        }
        tooltip.innerHTML = "";
        tooltip.appendChild(dl);
        tooltip.hidden = false;
        positionTooltip(x, y);
    }

    function hideTooltip() {
        if (tooltip) tooltip.hidden = true;
    }

    function handlePaneMouseOver(event) {
        const mark = event.target.closest(".xwalk-mark");
        if (!mark) return;
        const matchId = mark.dataset.match;
        setActive(matchId, true);
        showTooltip(matchId, event.clientX, event.clientY);
    }

    function handlePaneMouseOut(event) {
        const mark = event.target.closest(".xwalk-mark");
        if (!mark) return;
        const related = event.relatedTarget;
        if (related && mark.contains(related)) return;
        setActive(mark.dataset.match, false);
        hideTooltip();
    }

    function handlePaneMouseMove(event) {
        if (!tooltip || tooltip.hidden) return;
        positionTooltip(event.clientX, event.clientY);
    }

    for (const pane of [sourcePre, fhirPre]) {
        if (!pane) continue;
        pane.addEventListener("mouseover", handlePaneMouseOver);
        pane.addEventListener("mouseout", handlePaneMouseOut);
        pane.addEventListener("mousemove", handlePaneMouseMove);
    }

    if (generateBtn && messageTypeSelect && textarea) {
        generateBtn.addEventListener("click", async () => {
            const [messageType, triggerEvent] = messageTypeSelect.value.split("|");
            setBusy(generateBtn, true);
            try {
                const response = await fetch(
                    `/api/generate?message_type=${encodeURIComponent(messageType)}&trigger_event=${encodeURIComponent(triggerEvent)}`
                );
                const data = await response.json();
                if (!response.ok) {
                    showError(data.error.category, data.error.message);
                    return;
                }
                textarea.value = data.hl7_text;
                showEditableSource();
            } catch (err) {
                showError("Network error", String(err));
            } finally {
                setBusy(generateBtn, false);
            }
        });
    }

    if (fileInput && textarea) {
        fileInput.addEventListener("change", () => {
            const file = fileInput.files[0];
            if (!file) return;
            const reader = new FileReader();
            reader.onload = () => {
                textarea.value = reader.result;
                fileInput.value = "";
                showEditableSource();
            };
            reader.readAsText(file);
        });
    }

    if (!form || !textarea) return;

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const submitter = event.submitter;
        setBusy(submitter, true);
        try {
            const response = await fetch("/api/data-specification", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    hl7_text: textarea.value,
                    deduplicate: dedupCheckbox ? dedupCheckbox.checked : false,
                }),
            });
            const data = await response.json();
            if (!response.ok) {
                showError(data.error.category, data.error.message);
                return;
            }
            showCrosswalk(data.report, data.highlighting, data.deduplication);
        } catch (err) {
            showError("Network error", String(err));
        } finally {
            setBusy(submitter, false);
        }
    });
});
