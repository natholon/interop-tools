// A small, self-contained script for the Data Specification page - kept
// separate from app.js (not sharing its scope, since this app has no
// module system) rather than gated additions inside app.js's own
// DOMContentLoaded listener, since this page's own panes never need to be
// added to index.html's show*() hidden-toggle chains or vice versa - see
// CLAUDE.md's own Data Specification section for why a genuinely separate
// page (not a new pane on index.html) was chosen specifically to sidestep
// that recurring bug class.

document.addEventListener("DOMContentLoaded", () => {
    const form = document.getElementById("crosswalk-form");
    const textarea = document.getElementById("hl7_text");
    const fileInput = document.getElementById("hl7_file");
    const generateBtn = document.getElementById("crosswalk-generate-sample");
    const messageTypeSelect = document.getElementById("message-type-select");
    const errorPane = document.getElementById("crosswalk-error-pane");
    const outputPane = document.getElementById("crosswalk-output-pane");
    const unsupportedBanner = document.getElementById("crosswalk-unsupported-banner");
    const tableWrapper = document.getElementById("crosswalk-table-wrapper");
    const tableBody = document.getElementById("crosswalk-table-body");
    const rawJson = document.getElementById("crosswalk-raw-json");
    const toast = document.getElementById("toast");

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

    function showCrosswalk(report) {
        if (!outputPane || !tableBody) return;
        tableBody.innerHTML = "";

        if (unsupportedBanner) {
            if (report.unsupported) {
                unsupportedBanner.textContent = report.unsupported_reason || "Field-level provenance isn't implemented yet for this input.";
                unsupportedBanner.hidden = false;
            } else {
                unsupportedBanner.hidden = true;
            }
        }

        for (const entry of report.entries || []) {
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

            const pathCell = document.createElement("td");
            pathCell.textContent = entry.fhir_path;

            const sourceValueCell = document.createElement("td");
            sourceValueCell.textContent = entry.source_value ?? "";

            const valueCell = document.createElement("td");
            valueCell.textContent = entry.value ?? "";

            tr.append(locationCell, pathCell, sourceValueCell, valueCell);
            tableBody.appendChild(tr);
        }

        if (tableWrapper) tableWrapper.hidden = false;
        if (rawJson) rawJson.hidden = true;
        outputPane.hidden = false;
        if (errorPane) errorPane.hidden = true;
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
                body: JSON.stringify({ hl7_text: textarea.value }),
            });
            const data = await response.json();
            if (!response.ok) {
                showError(data.error.category, data.error.message);
                return;
            }
            showCrosswalk(data.report);
        } catch (err) {
            showError("Network error", String(err));
        } finally {
            setBusy(submitter, false);
        }
    });
});
