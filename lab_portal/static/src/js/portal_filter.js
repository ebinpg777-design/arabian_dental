/** Filter the case list in the browser.
 *
 * The stage a case is at is DERIVED from what the lab did — it is not a stored column,
 * so it cannot be a search domain and a server round-trip could not filter on it
 * without recomputing every case. Filtering the rendered page instead is instant, works
 * offline once loaded, and is honest about its scope: it narrows the page you are
 * looking at, which is what the count on each tile refers to.
 */
document.addEventListener("DOMContentLoaded", () => {
    const list = document.querySelector(".o_case_list");
    if (!list) {
        return;
    }
    const cards = Array.from(list.querySelectorAll(".o_case_card"));
    const chips = Array.from(document.querySelectorAll(".o_case_fchip"));
    const tiles = Array.from(document.querySelectorAll(".o_case_tile"));

    const apply = (stage) => {
        cards.forEach((card) => {
            const show = !stage || card.dataset.stage === stage;
            card.style.display = show ? "" : "none";
        });
        chips.forEach((c) => c.classList.toggle("active", (c.dataset.stage || "") === (stage || "")));
        tiles.forEach((t) => t.classList.toggle("o_tile_active", stage && t.dataset.stage === stage));

        let empty = document.querySelector(".o_case_empty_filter");
        const none = cards.every((c) => c.style.display === "none");
        if (none && !empty) {
            empty = document.createElement("div");
            empty.className = "o_case_empty_filter alert alert-light mt-2";
            empty.textContent = "No cases at this stage on this page.";
            list.after(empty);
        } else if (empty && !none) {
            empty.remove();
        }
    };

    chips.forEach((chip) =>
        chip.addEventListener("click", () => apply(chip.dataset.stage || ""))
    );
    // A tile is a filter too — tapping the number that says "3 ready" should show them.
    tiles.forEach((tile) =>
        tile.addEventListener("click", (ev) => {
            ev.preventDefault();
            const active = tile.classList.contains("o_tile_active");
            apply(active ? "" : tile.dataset.stage);
        })
    );
});
