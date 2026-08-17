const SEVERITY_ORDER = { error: 0, warning: 1, info: 2 };

function debounce(fn, delay) {
    let timer;
    return (...args) => {
        clearTimeout(timer);
        timer = setTimeout(() => fn(...args), delay);
    };
}

function escapeHtml(str) {
    return str.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

// Mirrors app/pipeline.py's own format sniff (is_x12 checked first since
// cheapest, then is_xml, else HL7v2 default) - a client-side preview only,
// the server is still the source of truth for what actually gets routed.
function detectFormat(text) {
    const stripped = text.replace(/^\uFEFF/, "").trimStart();
    if (!stripped) return null;
    if (stripped.startsWith("ISA")) return "X12 EDI";
    if (stripped.startsWith("<")) return "C-CDA XML";
    return "HL7v2";
}

function highlightJson(json) {
    const escaped = escapeHtml(json);
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

document.addEventListener("DOMContentLoaded", () => {
    const form = document.getElementById("convert-form");
    const textarea = document.getElementById("hl7_text");
    const fileInput = document.getElementById("hl7_file");
    const generateBtn = document.getElementById("generate-sample");
    const messageTypeSelect = document.getElementById("message-type-select");
    const outputPane = document.getElementById("output-pane");
    const outputCode = document.getElementById("output-code");
    const resourceChipRow = document.getElementById("resource-chip-row");
    const dedupSummaryEl = document.getElementById("dedup-summary");
    const deduplicateCheckbox = document.getElementById("deduplicate");
    const transformForm = document.getElementById("transform-form");
    const transformBundleTextarea = document.getElementById("transform_bundle_json");
    const transformTargetSelect = document.getElementById("transform-target-select");
    const transformErrorPane = document.getElementById("transform-error-pane");
    const transformOutputPane = document.getElementById("transform-output-pane");
    const transformOutputCode = document.getElementById("transform-output-code");
    const copyTransformOutputBtn = document.getElementById("copy-transform-output");
    const useBundleBtn = document.getElementById("use-bundle-for-transform");
    const errorPane = document.getElementById("error-pane");
    const validationPane = document.getElementById("validation-pane");
    const formatBadge = document.getElementById("format-badge");
    const toast = document.getElementById("toast");
    const copyOutputBtn = document.getElementById("copy-output");

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

    function updateFormatBadge() {
        if (!formatBadge) return;
        const format = detectFormat(textarea.value);
        if (!format) {
            formatBadge.hidden = true;
            return;
        }
        formatBadge.textContent = `Detected: ${format}`;
        formatBadge.hidden = false;
    }
    updateFormatBadge();
    textarea.addEventListener("input", debounce(updateFormatBadge, 150));

    function showError(category, message) {
        errorPane.innerHTML = "";
        const strong = document.createElement("strong");
        strong.textContent = category;
        const pre = document.createElement("pre");
        pre.textContent = message;
        errorPane.append(strong, pre);
        errorPane.hidden = false;
        outputPane.hidden = true;
        validationPane.hidden = true;
        if (useBundleBtn) useBundleBtn.hidden = true;
    }

    if (generateBtn && messageTypeSelect) {
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
                updateFormatBadge();
            } catch (err) {
                showError("Network error", String(err));
            } finally {
                setBusy(generateBtn, false);
            }
        });
    }

    if (fileInput) {
        fileInput.addEventListener("change", () => {
            const file = fileInput.files[0];
            if (!file) return;
            const reader = new FileReader();
            reader.onload = () => {
                textarea.value = reader.result;
                fileInput.value = "";
                updateFormatBadge();
            };
            reader.readAsText(file);
        });
    }

    function buildResourceChips(bundle) {
        if (!resourceChipRow) return;
        resourceChipRow.innerHTML = "";
        const counts = {};
        for (const entry of bundle.entry || []) {
            const type = entry.resource && entry.resource.resourceType;
            if (!type) continue;
            counts[type] = (counts[type] || 0) + 1;
        }
        const types = Object.keys(counts).sort();
        if (!types.length) {
            resourceChipRow.hidden = true;
            return;
        }
        for (const type of types) {
            const chip = document.createElement("span");
            chip.className = "chip";
            chip.textContent = type;
            const count = document.createElement("span");
            count.className = "chip-count";
            count.textContent = `×${counts[type]}`;
            chip.appendChild(count);
            resourceChipRow.appendChild(chip);
        }
        resourceChipRow.hidden = false;
    }

    function showResult(bundle, dedupSummary) {
        const json = JSON.stringify(bundle, null, 2);
        outputCode.innerHTML = highlightJson(json);
        outputCode.dataset.raw = json;
        buildResourceChips(bundle);
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
        outputPane.hidden = false;
        errorPane.hidden = true;
        validationPane.hidden = true;
        if (useBundleBtn) useBundleBtn.hidden = false;
    }

    if (copyOutputBtn) {
        copyOutputBtn.addEventListener("click", async () => {
            const raw = outputCode.dataset.raw || outputCode.textContent;
            try {
                await navigator.clipboard.writeText(raw);
                showToast("Copied Bundle JSON to clipboard");
            } catch (err) {
                showToast("Copy failed - select and copy manually");
            }
        });
    }

    function buildFindingsSummary(findings) {
        const counts = { error: 0, warning: 0, info: 0 };
        for (const f of findings) {
            if (counts[f.severity] !== undefined) counts[f.severity]++;
        }
        const summary = document.createElement("div");
        summary.className = "finding-summary";
        const parts = [];
        if (counts.error) parts.push(`<span class="count-error">${counts.error} error${counts.error === 1 ? "" : "s"}</span>`);
        if (counts.warning) parts.push(`<span class="count-warning">${counts.warning} warning${counts.warning === 1 ? "" : "s"}</span>`);
        if (counts.info) parts.push(`<span class="count-info">${counts.info} info</span>`);
        summary.innerHTML = parts.join(" · ");
        return { summary, counts };
    }

    function buildSeverityFilters(counts, list) {
        const wrap = document.createElement("div");
        wrap.className = "severity-filters";
        const options = [
            ["all", "All"],
            ["error", "Error"],
            ["warning", "Warning"],
            ["info", "Info"],
        ];
        for (const [key, labelText] of options) {
            if (key !== "all" && !counts[key]) continue;
            const btn = document.createElement("button");
            btn.type = "button";
            btn.className = "filter-chip";
            btn.textContent = labelText;
            btn.setAttribute("aria-pressed", key === "all" ? "true" : "false");
            btn.addEventListener("click", () => {
                wrap.querySelectorAll(".filter-chip").forEach((b) => b.setAttribute("aria-pressed", "false"));
                btn.setAttribute("aria-pressed", "true");
                for (const li of list.children) {
                    li.hidden = key !== "all" && li.dataset.severity !== key;
                }
            });
            wrap.appendChild(btn);
        }
        return wrap;
    }

    function showValidationResult(report) {
        validationPane.innerHTML = "";
        const heading = document.createElement("h2");
        heading.textContent = report.is_valid
            ? "Validation Report — no errors found"
            : "Validation Report — issues found";
        validationPane.appendChild(heading);

        if (!report.findings.length) {
            const p = document.createElement("p");
            p.textContent = "No issues found.";
            validationPane.appendChild(p);
        } else {
            const sorted = [...report.findings].sort(
                (a, b) => (SEVERITY_ORDER[a.severity] ?? 3) - (SEVERITY_ORDER[b.severity] ?? 3)
            );

            const list = document.createElement("ul");
            list.className = "findings-list";
            for (const finding of sorted) {
                const li = document.createElement("li");
                li.className = `finding finding-${finding.severity}`;
                li.dataset.severity = finding.severity;

                const severitySpan = document.createElement("span");
                severitySpan.className = "finding-severity";
                severitySpan.textContent = finding.severity.toUpperCase();

                const locationSpan = document.createElement("span");
                locationSpan.className = "finding-location";
                locationSpan.textContent = finding.segment
                    ? finding.field
                        ? `${finding.segment}-${finding.field}`
                        : finding.segment
                    : "(message)";

                const messageSpan = document.createElement("span");
                messageSpan.className = "finding-message";
                messageSpan.textContent = finding.message;

                li.append(severitySpan, locationSpan, messageSpan);
                list.appendChild(li);
            }

            const { summary, counts } = buildFindingsSummary(report.findings);
            validationPane.appendChild(summary);
            validationPane.appendChild(buildSeverityFilters(counts, list));
            validationPane.appendChild(list);
        }

        validationPane.hidden = false;
        outputPane.hidden = true;
        errorPane.hidden = true;
        if (useBundleBtn) useBundleBtn.hidden = true;
    }

    function showTransformError(category, message) {
        if (!transformErrorPane) return;
        transformErrorPane.innerHTML = "";
        const strong = document.createElement("strong");
        strong.textContent = category;
        const pre = document.createElement("pre");
        pre.textContent = message;
        transformErrorPane.append(strong, pre);
        transformErrorPane.hidden = false;
        if (transformOutputPane) transformOutputPane.hidden = true;
    }

    function showTransformResult(messageText) {
        if (!transformOutputCode || !transformOutputPane) return;
        // HL7v2's bare \r segment terminator doesn't reliably render as a
        // line break inside <pre>/<code> the way it does in a <textarea> -
        // display-only substitution (the copy button below copies this
        // same \n-terminated text, which app/hl7/parser.py::parse_message
        // already normalizes back to \r on the way in, so round-tripping
        // through this app's own Convert/Transform forms is unaffected).
        transformOutputCode.textContent = messageText.replace(/\r\n?/g, "\n");
        transformOutputPane.hidden = false;
        if (transformErrorPane) transformErrorPane.hidden = true;
    }

    if (useBundleBtn && outputCode && transformBundleTextarea) {
        useBundleBtn.addEventListener("click", () => {
            transformBundleTextarea.value = outputCode.dataset.raw || outputCode.textContent;
            transformBundleTextarea.scrollIntoView({ behavior: "smooth", block: "center" });
        });
    }

    if (copyTransformOutputBtn && transformOutputCode) {
        copyTransformOutputBtn.addEventListener("click", async () => {
            try {
                await navigator.clipboard.writeText(transformOutputCode.textContent);
                showToast("Copied generated message to clipboard");
            } catch (err) {
                showToast("Copy failed - select and copy manually");
            }
        });
    }

    if (transformForm && transformBundleTextarea && transformTargetSelect) {
        transformForm.addEventListener("submit", async (event) => {
            event.preventDefault();
            const submitter = event.submitter;
            const [targetFormat, rest] = transformTargetSelect.value.split(" ");
            const [targetType, targetTrigger] = (rest || "").split("^");
            setBusy(submitter, true);
            try {
                const response = await fetch("/api/transform", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        bundle_json: transformBundleTextarea.value,
                        target_format: targetFormat || "",
                        target_type: targetType || "",
                        target_trigger: targetTrigger || "",
                    }),
                });
                const data = await response.json();
                if (!response.ok) {
                    showTransformError(data.error.category, data.error.message);
                    return;
                }
                showTransformResult(data.message_text);
            } catch (err) {
                showTransformError("Network error", String(err));
            } finally {
                setBusy(submitter, false);
            }
        });
    }

    if (!form) return;

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const submitter = event.submitter;
        const isValidate = Boolean(submitter && submitter.formAction && submitter.formAction.endsWith("/validate"));
        const endpoint = isValidate ? "/api/validate" : "/api/convert";
        setBusy(submitter, true);
        try {
            const body = { hl7_text: textarea.value };
            if (!isValidate && deduplicateCheckbox) {
                body.deduplicate = deduplicateCheckbox.checked;
            }
            const response = await fetch(endpoint, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(body),
            });
            const data = await response.json();
            if (!response.ok) {
                showError(data.error.category, data.error.message);
                return;
            }
            if (isValidate) {
                showValidationResult(data.report);
            } else {
                showResult(data.bundle, data.deduplication);
            }
        } catch (err) {
            showError("Network error", String(err));
        } finally {
            setBusy(submitter, false);
        }
    });
});
