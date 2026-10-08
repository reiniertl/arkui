/*
 * web_scene_producer.h — the ArkWeb half of the two-producer design.
 *
 * WHY THIS EXISTS
 * In ArkUI's node tree a Web component is ONE FrameNode. A webview-based
 * reader and a webview-based video player are the same node. That is the
 * single largest blind spot in the ArkUI record, and with platform access it
 * is closable: ArkWeb is platform code too, so the same patch that reaches
 * ArkUI reaches the page engine.
 *
 * The contract: emit the SAME ark_scene_descriptor_t, with source
 * ARK_SRC_ARKWEB and the SAME window_id as the hosting ArkUI record, on the
 * same clock and at the same cadence. The aggregator folds the pair; the
 * fusion module never learns which subsystem spoke.
 *
 * WHERE THIS RUNS
 * The renderer side, where the document and layout live — not the browser
 * side in the app process. The record crosses to the app process the same way
 * other renderer telemetry does, and is published into the same ring.
 *
 * COST
 * Blink already computes essentially all of this for its own purposes. The
 * job is mapping, not measuring. Nothing here should add a tree walk that
 * Blink is not already doing; sample structure on commit, not per frame.
 */

#ifndef ARK_WEB_SCENE_PRODUCER_H
#define ARK_WEB_SCENE_PRODUCER_H

#include <cstdint>

#include "scene_descriptor.h"

namespace ark::scene {

/* Mapping from Blink state to the descriptor. Each row is a hook site in the
 * renderer; the left column is the field it fills.
 *
 *   dyn.media_playing/_audible   HTMLMediaElement state; the page scheduler's
 *                                audible flag covers background tabs.
 *   st.largest_leaf_frac/_aspect LCP element bounds. An LCP that is a video
 *                                element is a strong, cheap video signal.
 *   st.text_len_total/_largest   document text traversal, VIEWPORT-SCOPED.
 *                                Whole-document length is misleading on an
 *                                infinite feed.
 *   st.editable_count            form controls plus contenteditable.
 *   st.scroller_count/scroll_axis scroll containers; axis from their overflow.
 *   dyn.scroll_delta_px/_events  scroll offset updates.
 *   dyn.dirty_layout             layout invalidations per frame.
 *   dyn.dirty_render             compositor commits with no relayout.
 *   dyn.damage_permille_sum      compositor damage rect / viewport area.
 *   dyn.frames / dyn.vsyncs      BeginFrame received vs frames produced.
 *   dyn.text_mutations           character-data and child-list mutations on
 *                                text, which is the chat-insertion signal.
 *   dyn.nodes_created/_destroyed DOM node lifecycle; feed churn.
 *   st.role_hist[]               element -> ark_role_t, ONE table (below).
 *   st.opaque_frac               cross-origin frames and plugin content the
 *                                renderer cannot describe — the same coverage
 *                                honesty ArkUI owes about Web.
 *
 * Element -> role, the one table:
 *   text nodes, headings, p, span        -> ARK_ROLE_TEXT
 *   img, picture, CSS background images  -> ARK_ROLE_IMAGE
 *   video                                -> ARK_ROLE_VIDEO
 *   input, textarea, contenteditable     -> ARK_ROLE_EDITABLE
 *   button, a with button role           -> ARK_ROLE_BUTTON
 *   scroll containers                    -> ARK_ROLE_SCROLLER
 *   layout-only elements                 -> ARK_ROLE_CONTAINER
 *   canvas, WebGL contexts               -> ARK_ROLE_CANVAS
 *   cross-origin frames, plugins         -> ARK_ROLE_OPAQUE
 *   anything unmapped                    -> ARK_ROLE_OTHER
 */
class WebSceneProducer {
public:
    /* hostWindowId MUST equal the window_id the ArkUI collector uses for the
     * window hosting this Web component, or the fold silently fails and you
     * get two unrelated half-records. */
    WebSceneProducer(uint64_t hostWindowId, uint32_t pid);

    /* Frame path, renderer side. Same rules as ArkUI: counter bumps only. */
    void OnBeginFrame() noexcept;                     /* -> dyn.vsyncs */
    void OnFrameCommitted(uint16_t damagePermille) noexcept;
    void OnLayoutInvalidated(uint32_t nodes) noexcept;   /* -> dirty_layout */
    void OnPaintOnlyUpdate(uint32_t nodes) noexcept;     /* -> dirty_render */
    void OnScroll(uint32_t absDeltaPx) noexcept;
    void OnTextMutation() noexcept;
    void OnNodeLifecycle(bool created, bool destroyed) noexcept;

    /* State edges. */
    void SetMediaState(bool playing, bool audible) noexcept;
    void SetEditableFocused(bool focused) noexcept;      /* -> dyn.ime_visible */

    /* Structure, on document commit or significant DOM change — NOT per
     * frame. Viewport-scoped: what the user can see, not what the document
     * contains. */
    void SampleStructure(const ark_struct_t& st) noexcept;

    /* Same semantics as the ArkUI collector, including idle suppression and
     * seq stamping. Drive it from the same cadence so the pair of records
     * covers the same interval. */
    bool Flush(ark_scene_descriptor_t& out, uint8_t flushReason) noexcept;

    /* Called with the page URL; only the origin is copied into `route`. Never
     * copy a full URL into a telemetry record. */
    void SetOrigin(const char* origin) noexcept;

private:
    /* Implementation mirrors SceneCollector exactly — relaxed atomics, no
     * allocation, idle suppression, seq per (window_id, source). Sharing the
     * implementation is tempting but the two live in different repositories
     * with different rebase cadences; duplicating ~100 lines is cheaper than
     * a shared dependency across that boundary. */
    ark_scene_descriptor_t base_{};
    uint32_t seq_{0};
    uint64_t intervalStartNs_{0};
};

}  // namespace ark::scene

#endif /* ARK_WEB_SCENE_PRODUCER_H */
