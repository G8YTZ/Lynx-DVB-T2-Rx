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
