/*
 * tsgate.h - hold back a transport stream until the first H.264 keyframe.
 *
 * A receiver joining a stream mid-GOP otherwise hands the player seconds of
 * video it can't decode yet; the player stores it and plays it late, so the
 * wait for the keyframe becomes permanent delay. The gate passes PSI/SI
 * (PAT, PMT, SDT...) straight through, drops everything else until an H.264
 * SPS (which encoders send with each IDR) starts on the video PID, then opens
 * for good. Lynx DVB-T2 Receiver, G8YTZ, GPLv3.
 */
#pragma once
#include <stdint.h>
#include <string.h>

typedef struct {
    int open;           /* 1 once the keyframe has been seen */
    int pmt_pid;        /* from the PAT, -1 unknown */
    int video_pid;      /* from the PMT (stream type 0x1b), -1 unknown */
    int es_pids[16];    /* elementary streams from the PMT */
    int n_es;
    unsigned long dropped;
    uint8_t pes[64 * 188];   /* current video PES while closed (so we open on its start) */
    int npes;                /* packets held, -1 = not inside a PES we can use */
} tsgate;

static void tsgate_init(tsgate* g)
{
    memset(g, 0, sizeof *g);
    g->pmt_pid = g->video_pid = -1;
    g->npes = -1;
}

/* Section payload of a packet with payload_unit_start, or NULL. */
static const uint8_t* ts_section(const uint8_t* p, int* len)
{
    if (!(p[1] & 0x40)) return NULL;
    int off = 4;
    if (p[3] & 0x20) off += 1 + p[4];
    if (off >= 188) return NULL;
    off += 1 + p[off];                           /* pointer field */
    if (off + 3 > 188) return NULL;
    *len = 188 - off;
    return p + off;
}

static void tsgate_psi(tsgate* g, const uint8_t* p, int pid)
{
    int len;
    const uint8_t* s = ts_section(p, &len);
    if (!s) return;
    int slen = ((s[1] & 0x0f) << 8) | s[2];
    if (slen + 3 > len) slen = len - 3;
    if (pid == 0 && s[0] == 0x00) {              /* PAT: first programme's PMT */
        for (int i = 8; i + 4 <= slen + 3 - 4; i += 4) {
            int prog = (s[i] << 8) | s[i + 1];
            if (prog != 0) { g->pmt_pid = ((s[i + 2] & 0x1f) << 8) | s[i + 3]; break; }
        }
    } else if (pid == g->pmt_pid && s[0] == 0x02) {   /* PMT */
        int pil = ((s[10] & 0x0f) << 8) | s[11];
        int i = 12 + pil;
        g->n_es = 0;
        while (i + 5 <= slen + 3 - 4) {
            int type = s[i], epid = ((s[i + 1] & 0x1f) << 8) | s[i + 2];
            int eil = ((s[i + 3] & 0x0f) << 8) | s[i + 4];
            if (g->n_es < 16) g->es_pids[g->n_es++] = epid;
            if (type == 0x1b && g->video_pid < 0) g->video_pid = epid;
            i += 5 + eil;
        }
    }
}

/* 1 if this video packet's payload contains an H.264 SPS NAL (type 7). */
static int ts_has_sps(const uint8_t* p)
{
    int off = 4;
    if (p[3] & 0x20) off += 1 + p[4];
    for (int i = off; i + 3 < 188; i++)
        if (p[i] == 0 && p[i + 1] == 0 && p[i + 2] == 1 && (p[i + 3] & 0x1f) == 7) return 1;
    return 0;
}

/* --- what the picture actually is -------------------------------------------
 * A Pi's hardware decoder takes H.264 up to level 4.0/4.1; 1080p50 is level 4.2
 * and produces no picture at all, which from outside looks exactly like a weak
 * signal. Reading profile and level out of the SPS lets the receiver say so.
 * Interlaced coding is fine - UK Freeview HD is 1080i. */
typedef struct { int ok, profile, level, width, height, interlaced; } h264info;

typedef struct { const uint8_t* d; size_t n; size_t pos; } bitrd;

static unsigned br_u(bitrd* b, int bits)
{
    unsigned v = 0;
    while (bits--) {
        unsigned byte = (b->pos >> 3) < b->n ? b->d[b->pos >> 3] : 0;
        v = (v << 1) | ((byte >> (7 - (b->pos & 7))) & 1);
        b->pos++;
    }
    return v;
}

static unsigned br_ue(bitrd* b)
{
    int z = 0;
    while (br_u(b, 1) == 0 && z < 32) z++;
    return z ? ((1u << z) - 1 + br_u(b, z)) : 0;
}

static int br_se(bitrd* b)
{
    unsigned k = br_ue(b);
    return (k & 1) ? (int)((k + 1) / 2) : -(int)(k / 2);
}

/* Parse an unescaped SPS payload (after the NAL header byte). */
static h264info h264_parse_sps(const uint8_t* d, size_t n)
{
    h264info h; bitrd b = { d, n, 0 };
    memset(&h, 0, sizeof h);
    h.profile = (int)br_u(&b, 8);
    br_u(&b, 8);                       /* constraint flags + reserved */
    h.level = (int)br_u(&b, 8);
    br_ue(&b);                         /* seq_parameter_set_id */
    if (h.profile == 100 || h.profile == 110 || h.profile == 122 || h.profile == 244 ||
        h.profile == 44 || h.profile == 83 || h.profile == 86 || h.profile == 118 ||
        h.profile == 128 || h.profile == 138 || h.profile == 139 || h.profile == 134) {
        unsigned chroma = br_ue(&b);
        if (chroma == 3) br_u(&b, 1);
        br_ue(&b); br_ue(&b); br_u(&b, 1);
        if (br_u(&b, 1)) {             /* scaling matrices */
            int lists = (chroma != 3) ? 8 : 12;
            for (int i = 0; i < lists; i++)
                if (br_u(&b, 1)) {
                    int size = (i < 6) ? 16 : 64, last = 8, next = 8;
                    for (int j = 0; j < size; j++) {
                        if (next) next = (last + br_se(&b) + 256) % 256;
                        last = next ? next : last;
                    }
                }
        }
    }
    br_ue(&b);                         /* log2_max_frame_num_minus4 */
    unsigned poc = br_ue(&b);
    if (poc == 0) br_ue(&b);
    else if (poc == 1) {
        br_u(&b, 1); br_se(&b); br_se(&b);
        unsigned k = br_ue(&b);
        for (unsigned i = 0; i < k && i < 256; i++) br_se(&b);
    }
    br_ue(&b); br_u(&b, 1);            /* max_num_ref_frames, gaps_allowed */
    h.width = (int)(br_ue(&b) + 1) * 16;
    unsigned hmb = br_ue(&b) + 1;
    int frame_mbs_only = (int)br_u(&b, 1);
    h.interlaced = !frame_mbs_only;
    h.height = (int)hmb * 16 * (frame_mbs_only ? 1 : 2);
    if (!frame_mbs_only) br_u(&b, 1);
    br_u(&b, 1);                       /* direct_8x8_inference */
    if (br_u(&b, 1)) {                 /* cropping */
        unsigned l = br_ue(&b), r = br_ue(&b), t = br_ue(&b), bo = br_ue(&b);
        h.width -= (int)(l + r) * 2;
        h.height -= (int)(t + bo) * 2 * (frame_mbs_only ? 1 : 2);
    }
    h.ok = (h.width > 0 && h.height > 0 && h.level > 0);
    return h;
}

/* Find an SPS in one TS packet and read it. */
static int ts_read_sps(const uint8_t* p, h264info* out)
{
    int off = 4;
    if (p[3] & 0x20) off += 1 + p[4];
    for (int i = off; i + 4 < 188; i++) {
        if (!(p[i] == 0 && p[i + 1] == 0 && p[i + 2] == 1 && (p[i + 3] & 0x1f) == 7)) continue;
        uint8_t raw[160]; size_t w = 0; int zeros = 0;
        for (int j = i + 4; j < 188 && w < sizeof raw; j++) {
            uint8_t c = p[j];
            if (zeros >= 2 && c == 3) { zeros = 0; continue; }   /* emulation prevention */
            zeros = (c == 0) ? zeros + 1 : 0;
            raw[w++] = c;
        }
        h264info h = h264_parse_sps(raw, w);
        if (h.ok) { *out = h; return 1; }
    }
    return 0;
}

/* Filter n bytes (whole 188-byte packets) in place; returns bytes kept.
 * buf must have room for n + sizeof g->pes bytes (the held PES start is
 * released in front of the packet that opens the gate). */
static size_t tsgate_filter(tsgate* g, uint8_t* buf, size_t n, uint8_t* out)
{
    if (g->open) { if (out != buf) memcpy(out, buf, n); return n; }
    size_t w = 0;
    for (size_t r = 0; r + 188 <= n; r += 188) {
        const uint8_t* p = buf + r;
        if (p[0] != 0x47) continue;
        int pid = ((p[1] & 0x1f) << 8) | p[2];
        if (g->open) { memcpy(out + w, p, 188); w += 188; continue; }
        int es = 0;
        for (int k = 0; k < g->n_es; k++) if (g->es_pids[k] == pid) es = 1;
        if (pid < 0x20 || pid == g->pmt_pid) tsgate_psi(g, p, pid);
        if (pid == g->video_pid && g->video_pid >= 0) {
            if (p[1] & 0x40) g->npes = 0;                  /* a new PES starts here */
            if (g->npes >= 0) {
                if (g->npes < 64) { memcpy(g->pes + g->npes * 188, p, 188); g->npes++; }
                else g->npes = -1;                         /* too long before an SPS: skip it */
            }
            if (g->npes > 0 && ts_has_sps(p)) {            /* keyframe: open at its PES start */
                memcpy(out + w, g->pes, (size_t)g->npes * 188);
                w += (size_t)g->npes * 188;
                g->open = 1;
                continue;
            }
            g->dropped++;
            continue;
        }
        (void)es;
        if (pid < 0x20 || pid == g->pmt_pid) { memcpy(out + w, p, 188); w += 188; }   /* tables pass */
        else g->dropped++;
    }
    return w;
}
