/*
 * scene_collector.cpp — accumulator implementation.
 *
 * Runs in every process that loads the framework. Everything on the frame
 * path is a relaxed atomic bump; nothing allocates, nothing throws, nothing
 * can fail in a way the host app would notice.
 */

#include "scene_collector.h"

#include <algorithm>
#include <cstring>
#include <ctime>
#include <limits>

namespace ark::scene {
namespace {

/* ArkUI's PROPERTY_UPDATE_* flags, mirrored so this file compiles standalone
 * for host-side tests. Keep in sync with the property header in your tree. */
constexpr uint32_t kUpdateMeasureSelf = 1u << 0;
constexpr uint32_t kUpdateMeasure     = 1u << 1;
constexpr uint32_t kUpdateLayout      = 1u << 2;
constexpr uint32_t kUpdateRender      = 1u << 3;

inline uint64_t NowNs() noexcept {
    timespec ts{};
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return static_cast<uint64_t>(ts.tv_sec) * 1000000000ull +
           static_cast<uint64_t>(ts.tv_nsec);
}

inline void CopyField(char* dst, size_t cap, const char* src) noexcept {
    if (src == nullptr) { dst[0] = '\0'; return; }
    std::strncpy(dst, src, cap - 1);
    dst[cap - 1] = '\0';
}

template <typename T>
inline void BumpSat(std::atomic<T>& a, T by = 1) noexcept {
    T cur = a.load(std::memory_order_relaxed);
    if (cur > std::numeric_limits<T>::max() - by) return;  /* saturate */
    a.fetch_add(by, std::memory_order_relaxed);
}

}  // namespace

SceneCollector::SceneCollector(uint64_t windowId, uint32_t pid, ark_source_t source) {
    base_.abi = ARK_SCENE_ABI_VERSION;
    base_.source = static_cast<uint32_t>(source);
    base_.window_id = windowId;
    base_.pid = pid;
    intervalStartNs_ = NowNs();
}

void SceneCollector::SetIdentity(const char* bundle, const char* ability,
                                 const char* route) noexcept {
    CopyField(base_.bundle, sizeof(base_.bundle), bundle);
    CopyField(base_.ability, sizeof(base_.ability), ability);
    CopyField(base_.route, sizeof(base_.route), route);
}

/* Counted on every vsync, whether or not this window draws. frames/vsyncs is
 * the only cadence feature that survives a variable refresh rate. */
void SceneCollector::OnVsync() noexcept { BumpSat(vsyncs_); }

/* The measure/layout/render split is why this hook is worth its cost.
 * Render-only dirt with steady frames is a repainting surface (video, an
 * animation). Measure/layout dirt is structural churn (a list scrolling, a
 * message arriving). Those separate most target profiles on their own. */
void SceneCollector::OnDirtyNode(uint32_t mask) noexcept {
    if (mask & (kUpdateMeasure | kUpdateMeasureSelf)) BumpSat(dirtyMeasure_);
    if (mask & kUpdateLayout) BumpSat(dirtyLayout_);
    if (mask & kUpdateRender) BumpSat(dirtyRender_);
}

void SceneCollector::OnFrameEnd(uint16_t damagePermille) noexcept {
    BumpSat(frames_);
    BumpSat(damageSum_, static_cast<uint32_t>(damagePermille));
}

void SceneCollector::OnScroll(uint32_t absDeltaPx) noexcept {
    BumpSat(scrollDelta_, absDeltaPx);
    BumpSat(scrollEvents_);
}

/* Fires when arbitration resolves, which is before the rendering work the
 * gesture implies. The caller should follow with Flush(GESTURE_EDGE) so the
 * burst occupies its own interval instead of being averaged with the idle
 * either side of it. */
void SceneCollector::OnGestureRecognized(uint8_t kind, uint8_t targetRole) noexcept {
    BumpSat(gestures_);
    gestureLast_.store(kind, std::memory_order_relaxed);
    gestureRole_.store(targetRole, std::memory_order_relaxed);
}

void SceneCollector::OnTextMutation() noexcept { BumpSat(textMutations_); }

void SceneCollector::OnNodeLifecycle(bool created, bool destroyed, bool reused) noexcept {
    if (created) { BumpSat(created_); BumpSat(churnSinceStruct_); }
    if (destroyed) { BumpSat(destroyed_); BumpSat(churnSinceStruct_); }
    if (reused) BumpSat(reused_);
}

void SceneCollector::OnAnimationCount(uint16_t active) noexcept {
    uint16_t cur = animMax_.load(std::memory_order_relaxed);
    while (active > cur &&
           !animMax_.compare_exchange_weak(cur, active, std::memory_order_relaxed)) {}
}

void SceneCollector::SetMediaState(bool playing, bool audible) noexcept {
    mediaPlaying_.store(playing, std::memory_order_relaxed);
    mediaAudible_.store(audible, std::memory_order_relaxed);
}

void SceneCollector::SetImeVisible(bool visible) noexcept {
    imeVisible_.store(visible, std::memory_order_relaxed);
}

void SceneCollector::SetVisibility(uint8_t visibility) noexcept {
    visible_.store(visibility, std::memory_order_relaxed);
}

void SceneCollector::SetWindowArea(uint16_t areaPermille) noexcept {
    windowArea_.store(areaPermille, std::memory_order_relaxed);
}

void SceneCollector::SampleStructure(const ark_struct_t& st, StructTrigger) noexcept {
    base_.st = st;
    structDirty_.store(true, std::memory_order_relaxed);
    churnSinceStruct_.store(0, std::memory_order_relaxed);
}

/* Catches in-component paging: a Swiper or Tabs page change replaces the
 * visible content without changing the route, so navigation-only triggering
 * leaves the structure record describing a page that is gone. */
bool SceneCollector::ShouldResampleStructure() const noexcept {
    return churnSinceStruct_.load(std::memory_order_relaxed) >= kStructChurnThreshold;
}

bool SceneCollector::Flush(ark_scene_descriptor_t& out, uint8_t flushReason) noexcept {
    const uint32_t frames = frames_.exchange(0, std::memory_order_relaxed);
    const uint32_t vsyncs = vsyncs_.exchange(0, std::memory_order_relaxed);
    const uint32_t gestures = gestures_.exchange(0, std::memory_order_relaxed);
    const bool hadStruct = structDirty_.exchange(false, std::memory_order_relaxed);

    /* Idle windows are the common case; emitting them would dominate the ring
     * and tell the module nothing absence does not. An edge flush is always
     * emitted, because a short burst interval is exactly what we want to keep. */
    const bool isEdge = (flushReason != ARK_FLUSH_TIMER);
    if (frames == 0 && gestures == 0 && !hadStruct && !isEdge) {
        intervalStartNs_ = NowNs();
        return false;
    }

    out = base_;
    out.seq = ++seq_;
    out.flush_reason = flushReason;
    out.visible = visible_.load(std::memory_order_relaxed);
    out.window_area_permille = windowArea_.load(std::memory_order_relaxed);
    out.t_start_ns = intervalStartNs_;
    out.t_end_ns = NowNs();
    intervalStartNs_ = out.t_end_ns;

    ark_dyn_t& d = out.dyn;
    d.vsyncs = vsyncs;
    d.frames = frames;
    d.dirty_measure = dirtyMeasure_.exchange(0, std::memory_order_relaxed);
    d.dirty_layout = dirtyLayout_.exchange(0, std::memory_order_relaxed);
    d.dirty_render = dirtyRender_.exchange(0, std::memory_order_relaxed);
    d.scroll_delta_px = scrollDelta_.exchange(0, std::memory_order_relaxed);
    d.scroll_events = static_cast<uint16_t>(
        std::min<uint32_t>(scrollEvents_.exchange(0, std::memory_order_relaxed), 0xFFFFu));
    d.text_mutations = textMutations_.exchange(0, std::memory_order_relaxed);
    d.nodes_created = created_.exchange(0, std::memory_order_relaxed);
    d.nodes_destroyed = destroyed_.exchange(0, std::memory_order_relaxed);
    d.nodes_reused = reused_.exchange(0, std::memory_order_relaxed);
    d.damage_permille_sum = damageSum_.exchange(0, std::memory_order_relaxed);
    d.anim_active_max = animMax_.exchange(0, std::memory_order_relaxed);
    d.gestures = static_cast<uint16_t>(std::min<uint32_t>(gestures, 0xFFFFu));
    d.gesture_last = gestureLast_.exchange(ARK_GESTURE_NONE, std::memory_order_relaxed);
    d.gesture_target_role = gestureRole_.load(std::memory_order_relaxed);
    d.media_playing = mediaPlaying_.load(std::memory_order_relaxed) ? 1 : 0;
    d.media_audible = mediaAudible_.load(std::memory_order_relaxed) ? 1 : 0;
    d.ime_visible = imeVisible_.load(std::memory_order_relaxed) ? 1 : 0;
    return true;
}

/* ---- Structure walk -------------------------------------------------------
 *
 * INTEGRATION POINT. The body is a sketch: it needs real ArkUI headers and
 * the tags differ across releases. The three invariants in the header are
 * what must survive.
 */
void ArkUiStructureWalker::Walk(void* root, uint32_t windowW, uint32_t windowH,
                                ark_struct_t& out) {
    std::memset(&out, 0, sizeof(out));
    if (root == nullptr || windowW == 0 || windowH == 0) return;

    /* Pseudocode against the real tree:
     *
     * stack.push({root, depth=0});
     * while (!stack.empty()) {
     *   auto [node, depth] = stack.pop();
     *   out.node_count++;
     *   out.max_depth = max(out.max_depth, depth);
     *
     *   role = RoleForPattern(node->GetPattern());   // the ONE table;
     *                                                // unmapped -> ROLE_OTHER
     *   out.role_hist[role]++;
     *
     *   if (IsOpaque(node)) {                        // XComponent, Web
     *     out.opaque_count++;
     *     out.opaque_frac += RectPermille(node, windowW, windowH);
     *     continue;                                   // do NOT descend
     *   }
     *   if (role == ARK_ROLE_TEXT) {
     *     len = TextLengthOf(node);
     *     out.text_len_total += len;
     *     out.text_len_largest = max(out.text_len_largest, len);
     *   }
     *   if (role == ARK_ROLE_EDITABLE)  out.editable_count++;
     *   if (role == ARK_ROLE_SCROLLER) {
     *     out.scroller_count++;
     *     out.scroll_axis |= AxisOf(node);
     *   }
     *   if (node->IsLeaf()) {                         // LEAF, not any node
     *     frac = RectPermille(node, windowW, windowH);
     *     if (frac > out.largest_leaf_frac) {
     *       out.largest_leaf_frac = frac;
     *       out.largest_leaf_aspect = AspectMilli(node);
     *     }
     *   }
     *   for (child : node->GetChildren()) stack.push({child, depth + 1});
     * }
     */
    (void)out;
}

}  // namespace ark::scene
