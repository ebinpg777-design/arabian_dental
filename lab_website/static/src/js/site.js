/** @odoo-module */
/*
 * The public site's behaviour, as Odoo public interactions so they start and stop with
 * the page (and stay out of the way in the website editor). Nothing here is needed for
 * the page to read: every section renders complete without JavaScript, these only add
 * the reveal, the counting numbers, the marquee loop, the rails and the lightbox.
 */
import { Interaction } from "@web/public/interaction";
import { registry } from "@web/core/registry";

const reduceMotion = () =>
    window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/* Elements fade and rise into place as they scroll into view. */
export class LabReveal extends Interaction {
    static selector = ".o_lab_reveal";

    setup() {
        if (reduceMotion() || !("IntersectionObserver" in window)) {
            this.el.classList.add("is-in");
            return;
        }
        const observer = new IntersectionObserver(
            (entries) => {
                for (const entry of entries) {
                    if (entry.isIntersecting) {
                        entry.target.classList.add("is-in");
                        observer.unobserve(entry.target);
                    }
                }
            },
            { rootMargin: "0px 0px -8% 0px", threshold: 0.08 }
        );
        observer.observe(this.el);
        this.registerCleanup(() => observer.disconnect());
    }
}

/* A number counts up from zero the first time it is seen. */
export class LabCounter extends Interaction {
    static selector = "[data-lab-count]";

    setup() {
        const target = parseFloat(this.el.dataset.labCount);
        const done = () => (this.el.textContent = this.format(target));
        if (isNaN(target)) {
            return;
        }
        if (reduceMotion() || !("IntersectionObserver" in window)) {
            done();
            return;
        }
        this.el.textContent = "0";
        const observer = new IntersectionObserver((entries) => {
            if (entries.some((e) => e.isIntersecting)) {
                observer.disconnect();
                this.run(target);
            }
        }, { threshold: 0.5 });
        observer.observe(this.el);
        this.registerCleanup(() => observer.disconnect());
    }

    format(n) {
        return Math.round(n).toLocaleString("en-IN");
    }

    run(target) {
        const duration = 1400;
        const start = performance.now();
        const tick = () => {
            const t = Math.min(1, (performance.now() - start) / duration);
            const eased = 1 - Math.pow(1 - t, 3);
            this.el.textContent = this.format(target * eased);
            if (t < 1 && !this.isDestroyed) {
                this.waitForAnimationFrame(tick);
            }
        };
        this.waitForAnimationFrame(tick);
    }
}

/* The track is duplicated once so a translateX(-50%) loop has no seam. */
export class LabMarquee extends Interaction {
    static selector = ".o_lab_marquee";

    setup() {
        const track = this.el.querySelector(".o_lab_marquee_track");
        if (!track || track.dataset.labDoubled) {
            return;
        }
        track.dataset.labDoubled = "1";
        const clone = track.cloneNode(true);
        clone.setAttribute("aria-hidden", "true");
        this.insert(clone, this.el, "beforeend");
    }
}

/* A horizontal rail: the arrows scroll by one card; the track itself snaps. */
export class LabRail extends Interaction {
    static selector = ".o_lab_rail";
    dynamicContent = {
        ".o_lab_rail_prev": { "t-on-click": () => this.scrollBy(-1) },
        ".o_lab_rail_next": { "t-on-click": () => this.scrollBy(1) },
    };

    scrollBy(direction) {
        const track = this.el.querySelector(".o_lab_rail_track");
        const item = track && track.querySelector(".o_lab_rail_item");
        if (!track || !item) {
            return;
        }
        const gap = parseFloat(getComputedStyle(track).columnGap || getComputedStyle(track).gap) || 16;
        track.scrollBy({ left: direction * (item.getBoundingClientRect().width + gap), behavior: "smooth" });
    }
}

/* Clicking a gallery card puts its image in the shared lightbox before Bootstrap
   opens it (the anchor carries data-bs-toggle="modal", so opening is not our job). */
export class LabGallery extends Interaction {
    static selector = ".o_lab_gallery";
    dynamicContent = {
        ".o_lab_gallery_item": { "t-on-click": (ev) => this.show(ev) },
    };

    show(ev) {
        const card = ev.currentTarget;
        const box = document.getElementById("labLightbox");
        if (!box) {
            return;
        }
        const img = box.querySelector(".o_lab_lightbox_img");
        const cap = box.querySelector(".o_lab_lightbox_cap");
        img.src = card.dataset.src;
        img.alt = card.dataset.caption || "";
        cap.textContent = [card.dataset.caption, card.dataset.cat].filter(Boolean).join(" · ");
    }
}

/* The gallery's category chips hide what does not match. */
export class LabGalleryFilters extends Interaction {
    static selector = ".o_lab_gallery_filters";
    dynamicContent = {
        ".o_lab_chip_btn": { "t-on-click": (ev) => this.filter(ev) },
    };

    filter(ev) {
        const chip = ev.currentTarget;
        const cat = chip.dataset.cat || "";
        for (const other of this.el.querySelectorAll(".o_lab_chip_btn")) {
            other.classList.toggle("active", other === chip);
        }
        const gallery = this.el.parentElement.querySelector(".o_lab_gallery");
        if (!gallery) {
            return;
        }
        for (const item of gallery.querySelectorAll(".o_lab_gallery_item")) {
            const hide = cat && item.dataset.cat !== cat;
            item.classList.toggle("d-none", hide);
        }
    }
}

const category = registry.category("public.interactions");
category.add("lab_website.reveal", LabReveal);
category.add("lab_website.counter", LabCounter);
category.add("lab_website.marquee", LabMarquee);
category.add("lab_website.rail", LabRail);
category.add("lab_website.gallery", LabGallery);
category.add("lab_website.gallery_filters", LabGalleryFilters);
