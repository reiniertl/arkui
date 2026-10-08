/*
 * scene_collector.h — in-process accumulator, one per window.
 *
 * Lives in the APP process, because that is where ArkUI runs. With platform
 * access the framework is patched once and every forked app process inherits
 * it — no app is touched, recompiled, or aware. The corollary is that this
 * code runs in EVERY process that loads the framework, including system UI,
 * the launcher and dialogs, so:
 *
 *   - the frame path is relaxed atomics and nothing else;
 *   - every hook must be exception-free and allocation-free;
 *   - a fault here is a fault in every app on the device.
 *
 * Division of labour:
 *   On*()                 — frame path. Counter bumps only.
 *   SampleStructure()     — transitions. May walk the tree.
 *   ShouldResampleStructure() — churn threshold, polled by the pipeline.
 *   Flush(reason)         — timer or transition edge. Writes one record.
 */

#ifndef ARK_SCENE_COLLECTOR_H
#define ARK_SCENE_COLLECTOR_H

#include <atomic>
#include <cstdint>

#include "scene_descriptor.h"

namespace ark::scene {

/* Reason a structural sample was triggered. ChurnThreshold is not optional:
 * in-component paging (Swiper, Tabs) replaces the visible content without
 * changing the route, so navigation-only triggering leaves the structure
 * record describing a page that is no longer on screen. */
enum class StructTrigger : uint8_t {
    Navigation,
    Foreground,
    ChurnThreshold,
    Periodic,
};

/* Nodes created+destroyed since the last structure sample before we consider
 * the structure stale. Tuned low enough to catch a page swap, high enough to
 * ignore ordinary list recycling. Validate against a Swiper trace. */
inline constexpr uint32_t kStructChurnThreshold = 48;

class SceneCollector {
public:
    SceneCollector(uint64_t windowId, uint32_t pid, ark_source_t source);

    /* ---- Frame path. Hot. ------------------------------------------------
     *
     * ArkUI hook points (verify names against your tree; they move across
     * releases):
     *
     *   OnVsync          <- the vsync callback, BEFORE deciding whether this
     *                       window produces a frame. frames/vsyncs is the only
     *                       interpretable cadence feature: 30 frames in 500ms
     *                       is saturation at 60Hz and half rate at 120Hz, and
     *                       with variable refresh the panel rate moves inside
     *                       a session, so this cannot be divided out later.
     *
     *   OnDirtyNode      <- node dirty marking, after the flag is known. Pass
     *                       the PROPERTY_UPDATE_* mask: the measure/layout vs
     *                       render split is the single most valuable signal
     *                       available here and the pipeline computes it anyway.
     *
     *   OnFrameEnd       <- pipeline flush, once the frame is committed.
     *                       damagePermille from the dirty region the render
     *                       context already has.
     *
     *   OnScroll         <- ANY pattern with a translating offset. Not only
     *                       the scroll family: Swiper and Tabs carry their own
     *                       offsets and are not scroll patterns, so hooking
     *                       only Scroll/List makes paging gestures invisible.
     *
     *   OnGestureRecognized <- recognizer arbitration, when a recognizer wins.
     *                       Fires BEFORE the rendering work it implies, which
     *                       makes it the only anticipatory signal in the set.
     *                       Caller should also Flush(GESTURE_EDGE) so the
     *                       burst is not smeared across a timer boundary.
     *
     *   OnTextMutation   <- text pattern content set.
     *   OnNodeLifecycle  <- node ctor/dtor and the reuse-pool hand-back.
     *   OnAnimationCount <- render context, in-flight property animations.
     */
    void OnVsync() noexcept;
    void OnDirtyNode(uint32_t propertyUpdateMask) noexcept;
    void OnFrameEnd(uint16_t damagePermille) noexcept;
    void OnScroll(uint32_t absDeltaPx) noexcept;
    void OnGestureRecognized(uint8_t gestureKind, uint8_t targetRole) noexcept;
    void OnTextMutation() noexcept;
    void OnNodeLifecycle(bool created, bool destroyed, bool reused) noexcept;
    void OnAnimationCount(uint16_t active) noexcept;

    /* ---- State edges. Cold. ---- */
    void SetMediaState(bool playing, bool audible) noexcept;
    void SetImeVisible(bool visible) noexcept;
    void SetVisibility(uint8_t visibility) noexcept;        /* ark_visibility_t */
    void SetWindowArea(uint16_t areaPermille) noexcept;
    void SetIdentity(const char* bundle, const char* ability, const char* route) noexcept;

    /* ---- Structure. Cold, may walk the tree. ----
     * Caller supplies a filled ark_struct_t; see ArkUiStructureWalker. Keeping
     * the walk outside the collector lets ArkWeb supply its own without
     * sharing ArkUI headers. */
    void SampleStructure(const ark_struct_t& st, StructTrigger why) noexcept;

    /* Polled by the pipeline at a convenient cold point (end of flush). True
     * once enough nodes have churned that the structure record is stale. */
    bool ShouldResampleStructure() const noexcept;

    /* ---- Report. ----
     * Snapshots and resets the dynamic counters, leaves structure intact,
     * stamps seq. Returns false if nothing happened in the interval — idle
     * windows are the common case and the ring is a shared resource.
     * A gesture-edge or page-change flush is emitted even when short. */
    bool Flush(ark_scene_descriptor_t& out, uint8_t flushReason) noexcept;

private:
    ark_scene_descriptor_t base_{};

    /* Relaxed atomics: the frame path and the flush timer may be on different
     * threads and we tolerate a torn interval boundary. Losing a few counts at
     * a 500ms edge is irrelevant to a classifier and far cheaper than
     * synchronising the frame path. */
    std::atomic<uint32_t> vsyncs_{0};
    std::atomic<uint32_t> frames_{0};
    std::atomic<uint32_t> dirtyMeasure_{0};
    std::atomic<uint32_t> dirtyLayout_{0};
    std::atomic<uint32_t> dirtyRender_{0};
    std::atomic<uint32_t> scrollDelta_{0};
    std::atomic<uint32_t> scrollEvents_{0};
    std::atomic<uint32_t> textMutations_{0};
    std::atomic<uint32_t> created_{0};
    std::atomic<uint32_t> destroyed_{0};
    std::atomic<uint32_t> reused_{0};
    std::atomic<uint32_t> damageSum_{0};
    std::atomic<uint32_t> gestures_{0};
    std::atomic<uint16_t> animMax_{0};
    std::atomic<uint8_t>  gestureLast_{0};
    std::atomic<uint8_t>  gestureRole_{0};

    /* Churn since the last structure sample, separate from the per-interval
     * lifecycle counters so a Flush does not reset the staleness estimate. */
    std::atomic<uint32_t> churnSinceStruct_{0};

    std::atomic<bool> mediaPlaying_{false};
    std::atomic<bool> mediaAudible_{false};
    std::atomic<bool> imeVisible_{false};
    std::atomic<bool> structDirty_{false};
    std::atomic<uint8_t> visible_{ARK_VIS_FOREGROUND};
    std::atomic<uint16_t> windowArea_{1000};

    uint32_t seq_{0};
    uint64_t intervalStartNs_{0};
};

/* ---- ArkUI structure walk -------------------------------------------------
 * Transitions only. Walks the node tree once, mapping each node's pattern to
 * a normalised role.
 *
 * Three invariants that must survive any implementation:
 *   1. ONE pattern->role table. Not scattered comparisons. That table is the
 *      maintenance burden as components are added upstream, and it must cover
 *      the whole supported component set, not just what your test apps use —
 *      an unmapped pattern falls back to ARK_ROLE_OTHER, never nowhere.
 *   2. STOP descending at XComponent (and at Web, where a separate ArkWeb
 *      producer covers the inside). Record the rect fraction AND fill an
 *      ark_opaque_ref_t: kind, rect, aspect, and the join_id — the surface
 *      id for an XComponent, the window id for a Web node. Reporting the
 *      hole without the handle leaves the aggregator unable to decide which
 *      surface record describes the region.
 *   3. Track the largest LEAF, not the largest node. The root always fills the
 *      window; get this wrong and every window looks like fullscreen video.
 */
class ArkUiStructureWalker {
public:
    /* `root` is an OHOS::Ace::NG::FrameNode*, kept as void* so this header does
     * not drag ArkUI internals into consumers that only read descriptors. */
    static void Walk(void* root, uint32_t windowW, uint32_t windowH, ark_struct_t& out);
};

}  // namespace ark::scene

#endif /* ARK_SCENE_COLLECTOR_H */
