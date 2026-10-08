/*
 * scene_descriptor.h — the common scene profile emitted by every Ark subsystem.
 *
 * One shape, several producers. ArkUI and ArkWeb are both platform code, so a
 * single framework patch reaches every app without touching any app. They
 * normalise into this struct so the fusion module never needs to know which
 * subsystem observed the window — which is what lets a webview-based reader
 * classify through the same path as a native one.
 *
 * Fixed size, POD, no allocation: written into a shared-memory ring from the
 * app process on a timer or a transition edge, never IPC'd per frame.
 *
 * ABI 2 adds seq, vsyncs, visible, window_area, flush_reason and the gesture
 * fields: without them a trace cannot be interpreted afterwards.
 * ABI 6 adds the opaque-role hint: what an XComponent probably contains,
 * guessed from its declared attributes. See ark_opaque_ref_t.
 * ABI 3 adds opaque_refs — join keys for the regions ArkUI cannot see into,
 * so a producer that CAN see them has something to match on.
 * ABI 4 adds the scene tuple: class, candidate set, confidence, and the
 * modifier bitmask that says what state the scene is IN and what work is
 * already committed.
 * ABI 5 adds ark_scene_event_t, the low-latency channel. An interval
 * record cannot carry a gesture: by the time it flushes the burst is over.
 */

#ifndef ARK_SCENE_DESCRIPTOR_H
#define ARK_SCENE_DESCRIPTOR_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define ARK_SCENE_ABI_VERSION 6u

/* Which subsystem produced this record. A window may produce several per
 * interval (one from ArkUI, one from ArkWeb for an embedded page); the
 * aggregator folds them by window_id. */
typedef enum {
    ARK_SRC_ARKUI      = 0,
    ARK_SRC_ARKWEB     = 1,
    ARK_SRC_XCOMPONENT = 2,  /* opaque leaf, described by its attributes only */
    ARK_SRC_COMPOSITE  = 3,  /* produced by the aggregator, not a collector */
} ark_source_t;

/* Normalised roles. Source-independent by design: an ArkUI Text and a Blink
 * text node are both ARK_ROLE_TEXT; an ArkUI List and a Blink scroll container
 * are both ARK_ROLE_SCROLLER.
 *
 * Positional in role_hist, so adding a role is an ABI bump. Keep it coarse —
 * a richer vocabulary buys less than it costs, because the discrimination
 * lives in the dynamics. An unmapped pattern lands in ARK_ROLE_OTHER, never
 * silently nowhere. */
typedef enum {
    ARK_ROLE_TEXT = 0,
    ARK_ROLE_IMAGE,
    ARK_ROLE_VIDEO,       /* a real media surface, not a thumbnail */
    ARK_ROLE_EDITABLE,    /* TextInput, TextArea, contenteditable, <input> */
    ARK_ROLE_BUTTON,
    ARK_ROLE_SCROLLER,    /* List, Grid, WaterFlow, Scroll, Swiper, overflow:auto */
    ARK_ROLE_CONTAINER,   /* layout-only: Column, Row, Stack, div */
    ARK_ROLE_CANVAS,      /* Canvas, <canvas>, custom drawing */
    ARK_ROLE_OPAQUE,      /* XComponent, or a surface an app owns */
    ARK_ROLE_OTHER,
    ARK_ROLE__COUNT
} ark_role_t;

/* Window visibility. Without this a background music player producing frames
 * is indistinguishable from a foreground scene. Sourced from the window
 * manager, not inferred. */
typedef enum {
    ARK_VIS_BACKGROUND = 0,
    ARK_VIS_OCCLUDED   = 1,
    ARK_VIS_FOREGROUND = 2,
} ark_visibility_t;

/* Why the interval ended. A 40ms gesture-edge interval means something very
 * different from a 500ms timer one, and a config-change flush is a relayout
 * storm rather than structural churn. Without this a rotation reads as a
 * scene. */
typedef enum {
    ARK_FLUSH_TIMER         = 0,
    ARK_FLUSH_GESTURE_EDGE  = 1,  /* gesture recognised, or gesture ended */
    ARK_FLUSH_PAGE_CHANGE   = 2,  /* navigation, or in-component paging */
    ARK_FLUSH_CONFIG_CHANGE = 3,  /* rotation, fold, density, IME resize */
    ARK_FLUSH_VISIBILITY    = 4,  /* foreground/background transition */
    ARK_FLUSH_SHUTDOWN      = 5,
} ark_flush_reason_t;

/* Gesture kinds, as the recognizer resolves them. This is the only genuinely
 * anticipatory signal available here: arbitration completes before the
 * rendering work it implies arrives. */
typedef enum {
    ARK_GESTURE_NONE = 0,
    ARK_GESTURE_TAP,
    ARK_GESTURE_LONG_PRESS,
    ARK_GESTURE_PAN_H,
    ARK_GESTURE_PAN_V,
    ARK_GESTURE_PINCH,
    ARK_GESTURE_ROTATE,
    ARK_GESTURE_SWIPE,
    ARK_GESTURE_DRAG,
    ARK_GESTURE_OTHER,
} ark_gesture_t;

/* A reference to something ArkUI cannot see into, carrying enough identity
 * for a producer that CAN see it to join on.
 *
 * This is the difference between reporting a hole and handing over a handle.
 * `join_id` is the key another subsystem already knows:
 *
 *   ARK_OPAQUE_XCOMPONENT -> the surface id. render_service knows its submit
 *                            rate, damage and buffer format by that id; the
 *                            media framework knows whether a player or camera
 *                            is bound to it.
 *   ARK_OPAQUE_WEB        -> the window id the ArkWeb producer stamps on its
 *                            own record for the same region.
 *
 * Without this, the aggregator can see that 940 per-mille of a window was
 * opaque and still have no way to decide WHICH surface record describes it. */
typedef enum {
    ARK_OPAQUE_XCOMPONENT = 0,
    ARK_OPAQUE_WEB        = 1,
    ARK_OPAQUE_OTHER      = 2,
} ark_opaque_kind_t;

#define ARK_MAX_OPAQUE_REFS 4u

/* What an XComponent was declared as. SURFACE is composited by render_service
 * on its own; TEXTURE is consumed back into the UI tree, which costs an extra
 * pass and is worth distinguishing for exactly that reason. */
typedef enum {
    ARK_XC_UNKNOWN   = 0,
    ARK_XC_SURFACE   = 1,
    ARK_XC_COMPONENT = 2,
    ARK_XC_TEXTURE   = 3,
    ARK_XC_NODE      = 4,
} ark_xc_type_t;

/* A GUESS about what is behind the surface, from attributes ArkUI can read
 * without seeing a single pixel. Never authoritative: the media framework and
 * the buffer queue know for certain, and this exists so the aggregator has
 * something to work with before it joins to them - and a prior to weigh their
 * answer against. */
typedef enum {
    ARK_HINT_UNKNOWN = 0,
    ARK_HINT_VIDEO,
    ARK_HINT_CAMERA,
    ARK_HINT_GAME,
    ARK_HINT_MAP,
    ARK_HINT_CHART,
    ARK_HINT_AR,
    ARK_HINT_AUDIO_VIS,
} ark_opaque_hint_t;

/* Where the hint came from. The aggregator should trust these differently:
 * a DRM flag is a fact, a substring in a developer-chosen id is a guess. */
enum {
    ARK_HF_SECURE        = 1u << 0,  /* enableSecure - DRM. Strong video signal */
    ARK_HF_HDR           = 1u << 1,  /* hdrBrightness set */
    ARK_HF_NAME_MATCH    = 1u << 2,  /* the component id matched a keyword */
    ARK_HF_LIBRARY_MATCH = 1u << 3,  /* the native library name matched */
    ARK_HF_A11Y_MATCH    = 1u << 4,  /* an accessibility label matched */
    ARK_HF_SIBLING_MATCH = 1u << 5,  /* surrounding components implied the role */
    ARK_HF_ASPECT_MATCH  = 1u << 6,  /* rect aspect matched a video ratio */
};

typedef struct {
    uint8_t  kind;          /* ark_opaque_kind_t */
    uint8_t  xc_type;       /* ark_xc_type_t */
    uint16_t rect_permille; /* of window area */
    uint16_t aspect_milli;  /* width/height * 1000 */
    uint8_t  hint;          /* ark_opaque_hint_t - a guess, see above */
    uint8_t  hint_conf;     /* 0..100. Treat <50 as "no better than a prior" */
    uint64_t join_id;       /* surface id, or window id for Web; 0 = unknown */

    /* FNV-1a of the developer-chosen component id, and of the native library
     * registered for it. Hashes rather than strings on purpose: these are app
     * internals and a power trace is not the place for them, while a hash
     * still joins the same surface across records and across runs. The
     * collector may log the plaintext once per (bundle, id) on a debug
     * channel when someone is building the dictionary. */
    uint32_t name_hash;
    uint32_t library_hash;

    uint16_t hint_flags;    /* ARK_HF_* - which evidence fired */
    uint16_t _pad;
    uint32_t _pad2;
} ark_opaque_ref_t;

/* ---- The scene tuple ---------------------------------------------------
 *
 * Emitted as: { class, candidates, confidence, modifiers, pending_dirty }
 *
 * `class` is the single best structural reading. `candidates` is the set the
 * tree narrows to — identical to `class` where the tree is decisive, wider
 * where it is not. A fullscreen surface is honestly {VIDEO, GAME, MAP,
 * CAMERA}, and saying so is more useful than guessing one.
 *
 * ArkUI never emits a policy. It emits structure and state; the aggregator
 * decides what either is worth. */
typedef enum {
    ARK_SCENE_UNCLASSIFIED = 0,
    ARK_SCENE_SPARSE,
    ARK_SCENE_MEDIA_PLAYER,     /* surface + transport controls */
    ARK_SCENE_AUDIO_PLAYER,     /* transport + artwork, no surface */
    ARK_SCENE_CALL_VIDEO,
    ARK_SCENE_CAPTURE,          /* camera preview */
    ARK_SCENE_IMMERSIVE_SURFACE,/* surface fills a near-empty tree */
    ARK_SCENE_WEB_CONTENT,
    ARK_SCENE_CHAT,
    ARK_SCENE_FORM,
    ARK_SCENE_EDITOR,
    ARK_SCENE_MEDIA_VIEW,       /* one dominant image */
    ARK_SCENE_GALLERY_GRID,
    ARK_SCENE_ICON_PAGER,       /* launcher, app drawer */
    ARK_SCENE_FEED,
    ARK_SCENE_READING,
    ARK_SCENE_PAGING,
    ARK_SCENE_LIST,
    ARK_SCENE_DIALOG,
    ARK_SCENE_SPLASH_LOADING,
    ARK_SCENE__COUNT
} ark_scene_class_t;

/* Orthogonal to the class: what state the scene is in, and what is already
 * committed. A CALL_VIDEO that is STATIC is not the same workload as one that
 * is CHURNING, and the class alone cannot say which. */
typedef enum {
    /* --- activity, retrospective --- */
    ARK_MOD_STATIC            = 1u << 0,  /* no frames, no dirty nodes */
    ARK_MOD_ANIMATING         = 1u << 1,  /* frames, render-only dirt */
    ARK_MOD_LAYOUT_CHURN      = 1u << 2,  /* measure/layout dirt: the dear path */

    /* --- work happening outside this process on our behalf --- */
    ARK_MOD_RENDERER_ANIMATING= 1u << 3,  /* property animation in render_service
                                           * while this process reports no frames.
                                           * Without this, a settle looks idle. */

    /* --- committed, forward-looking --- */
    ARK_MOD_GESTURE_ACTIVE    = 1u << 4,  /* a recognizer has won; finger down */
    ARK_MOD_WORK_COMMITTED    = 1u << 5,  /* pending_dirty > 0 at flush: the next
                                           * frame's work is already decided */
    ARK_MOD_TRANSITIONING     = 1u << 6,  /* page or route change in flight */
    ARK_MOD_FIRST_FRAMES      = 1u << 7,  /* window just appeared; construction
                                           * cost, not steady state */

    /* --- interaction --- */
    ARK_MOD_IME_UP            = 1u << 8,
    ARK_MOD_CHROME_VISIBLE    = 1u << 9,  /* media controls shown: interacting */
    ARK_MOD_CHROME_HIDDEN     = 1u << 10, /* media controls faded: passive watch */

    /* --- attention --- */
    ARK_MOD_OCCLUDED          = 1u << 11,
    ARK_MOD_BACKGROUND        = 1u << 12, /* invisible AND still working:
                                           * a throttling candidate */
    ARK_MOD_PARTIAL_WINDOW    = 1u << 13, /* split screen or PiP, not fullscreen */

    /* --- coverage --- */
    ARK_MOD_OPAQUE_DOMINANT   = 1u << 14, /* most of the window is not ours */
} ark_scene_mod_t;

/* ---- Structure: sampled on transition, not per frame -------------------
 * Recomputed on navigation, foreground change, or when the node-churn
 * threshold trips — the last of which catches in-component paging, where a
 * Swiper or Tabs replaces the visible content without changing the route.
 * A full tree walk here is fine because it is rare and bounded. */
typedef struct {
    uint32_t node_count;
    uint16_t max_depth;
    uint16_t role_hist[ARK_ROLE__COUNT];  /* counts, saturating */

    uint32_t text_len_total;    /* sum of text content length, UTF-16 units */
    uint32_t text_len_largest;  /* longest single contiguous block */

    uint16_t editable_count;
    uint16_t scroller_count;
    uint8_t  scroll_axis;       /* 0 none, 1 vertical, 2 horizontal, 3 both */
    uint8_t  _pad0;

    /* Largest LEAF, as a fraction of the window. The root always fills the
     * window, so only a leaf filling it carries information. */
    uint16_t largest_leaf_frac;    /* per-mille of window area */
    uint16_t largest_leaf_aspect;  /* width/height * 1000, saturating */

    /* Opaque children, by area. High opaque_frac means this record's structure
     * is not trustworthy on its own — expect an ArkWeb or surface record for
     * the same window and fold them. This is the coverage field. */
    uint16_t opaque_count;         /* total seen, may exceed ARK_MAX_OPAQUE_REFS */
    uint16_t opaque_frac;          /* per-mille of window area */

    /* The largest opaque children by area, with join keys. Bounded because
     * the record is fixed-size; opaque_count tells the aggregator whether any
     * were dropped. Camera typically fills two, a hybrid page one. */
    uint16_t opaque_ref_count;     /* how many entries below are valid */
    uint16_t _pad1;
    ark_opaque_ref_t opaque_refs[ARK_MAX_OPAQUE_REFS];
} ark_struct_t;

/* ---- Dynamics: accumulated every frame, reset on report ----------------
 * The dirty-flag split is the discriminative core and costs one atomic add,
 * because the pipeline computes the flag for its own reasons anyway. */
typedef struct {
    uint32_t vsyncs;            /* vsyncs available in the interval */
    uint32_t frames;            /* frames this window actually produced */

    uint32_t dirty_measure;     /* nodes marked for re-measure */
    uint32_t dirty_layout;      /* nodes marked for re-layout */
    uint32_t dirty_render;      /* nodes marked render-only */

    uint32_t scroll_delta_px;   /* absolute, summed; includes Swiper/Tabs */
    uint16_t scroll_events;
    uint16_t anim_active_max;   /* peak concurrent property animations.
                                 * Catches a settle running in the renderer
                                 * while this process reports zero frames. */

    uint32_t text_mutations;    /* text content changes; chat insertion signal */
    uint32_t nodes_created;
    uint32_t nodes_destroyed;
    uint32_t nodes_reused;      /* reuse-pool recycling; feed-scroll signal */

    uint32_t damage_permille_sum; /* per-frame damage area, summed; /frames = mean */

    uint16_t gestures;            /* recognised in this interval */
    uint8_t  gesture_last;        /* ark_gesture_t */
    uint8_t  gesture_target_role; /* ark_role_t the winning recognizer sat on */

    uint8_t  media_playing;     /* a media element or bound surface is active */
    uint8_t  media_audible;
    uint8_t  ime_visible;
    uint8_t  _pad1;
} ark_dyn_t;

typedef struct {
    uint32_t abi;               /* ARK_SCENE_ABI_VERSION */
    uint32_t source;            /* ark_source_t */

    /* Monotonic per (window_id, source). A jump means the ring dropped
     * records — which happens precisely when the system was busiest, so a
     * gap must never be mistaken for idle. */
    uint32_t seq;
    uint8_t  visible;           /* ark_visibility_t */
    uint8_t  flush_reason;      /* ark_flush_reason_t */
    uint16_t window_area_permille; /* of the display; picks a dominant scene
                                    * when split screen or PiP puts two live
                                    * windows up at once */

    uint64_t window_id;
    uint32_t pid;
    uint32_t _pad2;

    /* Identity. Not a scene, but a strong prior and essential for labelling
     * traces during the correlation phase. */
    char     bundle[96];
    char     ability[64];
    char     route[96];         /* page path, NavDestination name, or URL origin */

    /* Interval, CLOCK_MONOTONIC nanoseconds. Must share a clock with the
     * power sampler and every other producer, or nothing joins. */
    uint64_t t_start_ns;
    uint64_t t_end_ns;

    ark_struct_t st;
    ark_dyn_t    dyn;

    /* ---- the tuple ---- */
    uint16_t scene_class;       /* ark_scene_class_t */
    uint8_t  scene_confidence;  /* 0-100; low where the tree only narrows */
    uint8_t  _pad3;
    uint32_t scene_candidates;  /* bitmask over ark_scene_class_t */
    uint32_t modifiers;         /* bitmask over ark_scene_mod_t */

    /* Nodes marked dirty but NOT yet processed, sampled at flush. This is not
     * a prediction: it is work ArkUI has already committed to doing on the
     * next frame, known before that frame runs. The dyn.dirty_* counters are
     * retrospective; this one looks forward. */
    uint32_t pending_dirty;
} ark_scene_descriptor_t;

/* ---- The event channel ------------------------------------------------
 *
 * WHY THIS IS SEPARATE
 * The most valuable signal here — a recognizer resolving, committing work a
 * frame before it runs — has a lead time of about 8ms. An interval record
 * flushed every 100ms cannot deliver it in time to act on. The one thing a
 * governor most needs is the one thing the periodic channel is least able to
 * carry, so it gets its own path.
 *
 * Small, fixed, published the instant the event occurs. Same ring or a
 * separate lower-latency one; the record type distinguishes them.
 *
 * scene_class here is the class IN FORCE at the moment of the event, taken
 * from the last structure sample — not recomputed. For PAGE_CHANGE that means
 * it is the OUTGOING class, which is the useful one: knowing you are leaving a
 * FEED tells the consumer what is ending. The incoming class arrives with the
 * next interval record. */
typedef enum {
    ARK_EV_GESTURE_RECOGNIZED = 0, /* arbitration resolved; work now committed */
    ARK_EV_GESTURE_END        = 1, /* finger up; a settle may follow */
    ARK_EV_PAGE_CHANGE        = 2, /* route or in-component page committed */
    ARK_EV_FIRST_FRAME        = 3, /* window produced its first frame */
    ARK_EV_VISIBILITY_CHANGE  = 4,
    ARK_EV_MEDIA_STATE_CHANGE = 5, /* playback started or stopped */
} ark_event_kind_t;

typedef struct {
    uint32_t abi;               /* ARK_SCENE_ABI_VERSION */
    uint32_t seq;               /* own sequence, per window; gaps = dropped */

    uint64_t t_ns;              /* CLOCK_MONOTONIC, same clock as everything */
    uint64_t window_id;

    uint8_t  kind;              /* ark_event_kind_t */
    uint8_t  gesture;           /* ark_gesture_t, for the gesture kinds */
    uint8_t  target_role;       /* ark_role_t the winning recognizer sat on.
                                 * A pan on a SCROLLER commits relayout; the
                                 * same pan on an OPAQUE node commits nothing
                                 * of ours. Same gesture, opposite meaning. */
    uint8_t  visible;           /* ark_visibility_t */

    uint16_t scene_class;       /* ark_scene_class_t, standing at event time */
    uint16_t _pad;

    uint32_t modifiers;         /* the instantaneous subset: GESTURE_ACTIVE,
                                 * WORK_COMMITTED, TRANSITIONING, BACKGROUND */

    /* Nodes marked dirty and not yet processed, read at the instant of the
     * event. Magnitude of the work that is already decided. */
    uint32_t pending_dirty;

    /* How far ahead the committed work lands, in milliseconds. 0 means the
     * next frame.
     *
     * This is the field that makes the event worth acting on. A gesture
     * recognised on a pager commits a page transition whose settle animation
     * has a KNOWN duration, so the expensive moment is several hundred
     * milliseconds out, not 8. That clears any frequency-scaling loop and any
     * voltage ramp, which the one-frame case may not.
     *
     * Set it from what ArkUI actually knows: animation duration, transition
     * duration, scheduled task delay. Leave it 0 rather than guessing. */
    uint16_t horizon_ms;

    /* Expected duration of the committed work once it starts, where known —
     * an animation length, a transition. 0 = unknown. Lets the consumer pick
     * a decay rather than holding a boost until it times out. */
    uint16_t duration_ms;
} ark_scene_event_t;

/* ---- Suggested cadence for the interval channel -----------------------
 * Not enforced here; the collector owns it. Recorded so every producer uses
 * the same numbers and records line up across subsystems.
 *
 *   active (frames last interval)      100 ms
 *   quiet  (no frames, structure moved) on change only
 *   idle                                suppressed, no record
 *   edge   (gesture end, page change,
 *           config change)              immediate, closes the interval short
 *
 * 100ms on active is chosen so a ~800ms gesture resolves into its phases
 * rather than being averaged into two samples. */
#define ARK_INTERVAL_ACTIVE_MS 100u
#define ARK_INTERVAL_MAX_MS    1000u

#ifdef __cplusplus
}
#endif

#endif /* ARK_SCENE_DESCRIPTOR_H */
