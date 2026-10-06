/**
 * Oracle Academy Quiz Auto-Solver & Auto-Submitter
 * 
 * Paste this directly into the Developer Tools Console (F12 -> Console -> Enter)
 * in your Brave browser tab on the "Quiz: DP - Section 4" page.
 */
(() => {
    console.log("%c[Oracle Academy Auto-Solver Active]", "color: #00ff88; font-weight: bold; font-size: 16px;");

    function getDoc() {
        const frame = document.querySelector('iframe.apex-modal-dialog-frame') || document.querySelector('iframe');
        if (frame && frame.contentDocument && frame.contentDocument.body) {
            return frame.contentDocument;
        }
        return document;
    }

    async function sleep(ms) {
        return new Promise(resolve => setTimeout(resolve, ms));
    }

    async function runSolver() {
        // Step 1: Click "Take an Assessment" if present
        let doc = getDoc();
        let takeAssessmentBtn = Array.from(doc.querySelectorAll('button, a, span, .t-Button')).find(el => {
            const t = (el.innerText || el.textContent || '').trim().toLowerCase();
            return t.includes('take an assessment') || t.includes('take assessment') || t.includes('resume assessment');
        });

        if (takeAssessmentBtn) {
            console.log("-> Clicking 'Take an Assessment'...");
            takeAssessmentBtn.click();
            await sleep(4000);
        }

        // Step 2: Loop through questions
        let maxSteps = 50;
        let stepCount = 0;

        while (stepCount < maxSteps) {
            stepCount++;
            doc = getDoc();

            // Check for completion
            const bodyText = (doc.body.innerText || '').toLowerCase();
            if (bodyText.includes('assessment completed') || bodyText.includes('results') || bodyText.includes('assessment summary') || bodyText.includes('grade')) {
                console.log("%c[SUCCESS] Assessment completed!", "color: #00ff88; font-weight: bold; font-size: 14px;");
                alert("Oracle Quiz successfully completed!");
                break;
            }

            // Find question option inputs (radios/checkboxes)
            const inputs = Array.from(doc.querySelectorAll("input[type='radio'], input[type='checkbox']"));
            if (inputs.length > 0) {
                console.log(`[Question Step ${stepCount}] Found ${inputs.length} choices.`);
                
                // If nothing is selected, select the first option
                const anyChecked = inputs.some(i => i.checked);
                if (!anyChecked) {
                    inputs[0].click();
                    inputs[0].checked = true;
                    inputs[0].dispatchEvent(new Event('change', { bubbles: true }));
                    console.log("   -> Selected option 1");
                }
            }

            await sleep(1000);

            // Find Next / Submit / Save Progress button
            const actionButtons = Array.from(doc.querySelectorAll("button, input[type='button'], input[type='submit'], a.t-Button"));
            const nextBtn = actionButtons.find(b => {
                const txt = (b.innerText || b.value || b.textContent || '').trim().toLowerCase();
                return ['next', 'next question', 'continue', 'save progress', 'submit', 'finish'].some(k => txt === k || txt.startsWith(k));
            });

            if (nextBtn) {
                const btnText = (nextBtn.innerText || nextBtn.value || '').trim();
                console.log(`-> Clicking navigation: "${btnText}"`);
                nextBtn.click();
                await sleep(3000);
            } else {
                console.log("[INFO] No next/submit button detected. Checking if finished...");
                await sleep(2000);
                if (stepCount > 1) break;
            }
        }
    }

    runSolver();
})();
