/*
 * t2rx - tune a Linux DVB-T2 frontend (e.g. RPi TV HAT, Sony CXD2880) and
 * stream the whole transport stream to stdout, with signal status.
 * G8YTZ narrowband DVB-T2 project, GPLv3.
 *
 *   t2rx -f 436000000 -b 1.7 | <player>
 *
 * Waits for lock (re-tuning every 10 s), then copies /dev/dvb/adapterN/dvr0
 * to stdout. Exits with status 2 if lock is lost for --loss seconds, so a
 * supervisor can restart the player cleanly. Writes a one-line status file:
 *   state=LOCK sig=-77.6 cnr=28.3 rate=1.10 mod=QPSK fec=1/2 gi=1/8 fft=2K per=0 off=0
 */
#include <errno.h>
#include <fcntl.h>
#include <getopt.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <time.h>
#include <unistd.h>
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <linux/dvb/dmx.h>
#include <linux/dvb/frontend.h>
#include "tsgate.h"

static double now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + t.tv_nsec / 1e9; }

static int tune(int fe, unsigned freq, unsigned bw_hz, int plp)
{
    struct dtv_property p[6];
    struct dtv_properties ps = { .num = 0, .props = p };
    memset(p, 0, sizeof p);
    p[ps.num].cmd = DTV_CLEAR; ps.num++;
    p[ps.num].cmd = DTV_DELIVERY_SYSTEM; p[ps.num].u.data = SYS_DVBT2; ps.num++;
    p[ps.num].cmd = DTV_FREQUENCY; p[ps.num].u.data = freq; ps.num++;
    p[ps.num].cmd = DTV_BANDWIDTH_HZ; p[ps.num].u.data = bw_hz; ps.num++;
    p[ps.num].cmd = DTV_STREAM_ID; p[ps.num].u.data = plp; ps.num++;
    p[ps.num].cmd = DTV_TUNE; ps.num++;
    return ioctl(fe, FE_SET_PROPERTY, &ps);
}

/* Signal strength (dBm) and C/N (dB) from the DVBv5 statistics, if available. */
static void stats(int fe, double* sig, double* cnr)
{
    struct dtv_property p[2];
    struct dtv_properties ps = { .num = 2, .props = p };
    memset(p, 0, sizeof p);
    p[0].cmd = DTV_STAT_SIGNAL_STRENGTH;
    p[1].cmd = DTV_STAT_CNR;
    *sig = *cnr = -999;
    if (ioctl(fe, FE_GET_PROPERTY, &ps) < 0) return;
    if (p[0].u.st.len && p[0].u.st.stat[0].scale == FE_SCALE_DECIBEL) *sig = p[0].u.st.stat[0].svalue / 1000.0;
    if (p[1].u.st.len && p[1].u.st.stat[0].scale == FE_SCALE_DECIBEL) *cnr = p[1].u.st.stat[0].svalue / 1000.0;
}

/* Signalled DVB-T2 parameters (valid once locked). */
static void l1(int fe, char* mod, char* fec, char* gi, char* fft)
{
    static const char* M[] = { [QPSK] = "QPSK", [QAM_16] = "16QAM", [QAM_64] = "64QAM", [QAM_256] = "256QAM" };
    struct dtv_property p[4];
    struct dtv_properties ps = { .num = 4, .props = p };
    memset(p, 0, sizeof p);
    p[0].cmd = DTV_MODULATION; p[1].cmd = DTV_INNER_FEC;
    p[2].cmd = DTV_GUARD_INTERVAL; p[3].cmd = DTV_TRANSMISSION_MODE;
    strcpy(mod, "-"); strcpy(fec, "-"); strcpy(gi, "-"); strcpy(fft, "-");
    if (ioctl(fe, FE_GET_PROPERTY, &ps) < 0) return;
    unsigned m = p[0].u.data;
    if (m <= QAM_256 && M[m]) strcpy(mod, M[m]);
    switch (p[1].u.data) {
    case FEC_1_2: strcpy(fec, "1/2"); break;  case FEC_3_5: strcpy(fec, "3/5"); break;
    case FEC_2_3: strcpy(fec, "2/3"); break;  case FEC_3_4: strcpy(fec, "3/4"); break;
    case FEC_4_5: strcpy(fec, "4/5"); break;  case FEC_5_6: strcpy(fec, "5/6"); break;
    }
    switch (p[2].u.data) {
    case GUARD_INTERVAL_1_4: strcpy(gi, "1/4"); break;    case GUARD_INTERVAL_1_8: strcpy(gi, "1/8"); break;
    case GUARD_INTERVAL_1_16: strcpy(gi, "1/16"); break;  case GUARD_INTERVAL_1_32: strcpy(gi, "1/32"); break;
    case GUARD_INTERVAL_1_128: strcpy(gi, "1/128"); break;
    case GUARD_INTERVAL_19_128: strcpy(gi, "19/128"); break;
    case GUARD_INTERVAL_19_256: strcpy(gi, "19/256"); break;
    }
    switch (p[3].u.data) {
    case TRANSMISSION_MODE_1K: strcpy(fft, "1K"); break;   case TRANSMISSION_MODE_2K: strcpy(fft, "2K"); break;
    case TRANSMISSION_MODE_4K: strcpy(fft, "4K"); break;   case TRANSMISSION_MODE_8K: strcpy(fft, "8K"); break;
    case TRANSMISSION_MODE_16K: strcpy(fft, "16K"); break; case TRANSMISSION_MODE_32K: strcpy(fft, "32K"); break;
    }
}

/* Carrier offset in kHz, from the patched driver; 0 if it is not available.
 * A narrowband tuner will pull in a signal well away from the frequency asked
 * for, so this says where the signal actually is. */
static int carrier_offset(void)
{
    FILE* f = fopen("/sys/module/cxd2880/parameters/nb_offset_khz", "r");
    int v = 0;
    if (!f) return 0;
    if (fscanf(f, "%d", &v) != 1) v = 0;
    fclose(f);
    return v;
}

/* Packet (block) errors since the last call, from the DVBv5 counters; -1 if not available. */
static long per_delta(int fe)
{
    static unsigned long long last = 0;
    static int have = 0;
    struct dtv_property p[1];
    struct dtv_properties ps = { .num = 1, .props = p };
    memset(p, 0, sizeof p);
    p[0].cmd = DTV_STAT_ERROR_BLOCK_COUNT;
    if (ioctl(fe, FE_GET_PROPERTY, &ps) < 0 || !p[0].u.st.len || p[0].u.st.stat[0].scale != FE_SCALE_COUNTER)
        return -1;
    unsigned long long v = p[0].u.st.stat[0].uvalue;
    long d = have && v >= last ? (long)(v - last) : 0;
    last = v; have = 1;
    return d;
}

static void usage(void)
{
    fprintf(stderr,
        "usage: t2rx -f FREQ_HZ [-b 1.7|MHz|Hz] [-a adapter] [-p plp] [-s status_file]\n"
        "            [-l loss_seconds] [-u udp_port] [-q]\n"
        "  -G  don't hold the stream back until the first H.264 keyframe\n"
        "  -u  send the TS as UDP (7 packets per datagram) to 127.0.0.1:port\n"
        "      instead of stdout - a live source for the player, so it never builds a backlog\n"
        "  -b  bandwidth given to the driver (default 1.7). For 1.35/2 MHz with the\n"
        "      patched cxd2880 driver, set nb_fs_hz first and pass -b 1.7.\n");
}

int main(int argc, char** argv)
{
    unsigned freq = 0, bw_hz = 1712000;
    int adapter = 0, plp = 0, quiet = 0, udp_port = 0, gate_on = 1;
    double loss = 5;
    const char* statf = NULL;
    int c;
    while ((c = getopt(argc, argv, "f:b:a:p:s:l:u:Gqh")) != -1) {
        switch (c) {
        case 'f': freq = (unsigned)strtoul(optarg, NULL, 10); break;
        case 'b': { double v = atof(optarg);
                    bw_hz = v < 100 ? (v > 1.69 && v < 1.72 ? 1712000 : (unsigned)(v * 1e6)) : (unsigned)v; } break;
        case 'a': adapter = atoi(optarg); break;
        case 'p': plp = atoi(optarg); break;
        case 's': statf = optarg; break;
        case 'l': loss = atof(optarg); break;
        case 'q': quiet = 1; break;
        case 'u': udp_port = atoi(optarg); break;
        case 'G': gate_on = 0; break;
        default: usage(); return 1;
        }
    }
    if (!freq) { usage(); return 1; }
    signal(SIGPIPE, SIG_IGN);

    char path[64];
    snprintf(path, sizeof path, "/dev/dvb/adapter%d/frontend0", adapter);
    int fe = open(path, O_RDWR);
    if (fe < 0) { perror(path); return 1; }
    snprintf(path, sizeof path, "/dev/dvb/adapter%d/demux0", adapter);
    int dmx = open(path, O_RDWR);
    if (dmx < 0) { perror(path); return 1; }
    ioctl(dmx, DMX_SET_BUFFER_SIZE, 1024 * 1024);
    struct dmx_pes_filter_params f = { .pid = 0x2000, .input = DMX_IN_FRONTEND,
        .output = DMX_OUT_TS_TAP, .pes_type = DMX_PES_OTHER, .flags = DMX_IMMEDIATE_START };
    if (ioctl(dmx, DMX_SET_PES_FILTER, &f) < 0) { perror("DMX_SET_PES_FILTER"); return 1; }
    snprintf(path, sizeof path, "/dev/dvb/adapter%d/dvr0", adapter);
    int dvr = open(path, O_RDONLY | O_NONBLOCK);
    if (dvr < 0) { perror(path); return 1; }

    if (tune(fe, freq, bw_hz, plp) < 0) { perror("FE_SET_PROPERTY"); return 1; }
    if (!quiet) fprintf(stderr, "t2rx: tuning %.3f MHz, bandwidth %u Hz, PLP %d\n", freq / 1e6, bw_hz, plp);

    int us = -1;
    struct sockaddr_in ua;
    if (udp_port) {
        us = socket(AF_INET, SOCK_DGRAM, 0);
        memset(&ua, 0, sizeof ua);
        ua.sin_family = AF_INET;
        ua.sin_port = htons(udp_port);
        ua.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        int sz = 1 << 20;
        setsockopt(us, SOL_SOCKET, SO_SNDBUF, &sz, sizeof sz);
    }
    static unsigned char pend[188 * 7];      /* UDP: whole 7-packet datagrams */
    size_t npend = 0;
    static unsigned char buf[188 * 1024];
    static unsigned char gbuf[188 * 1024 + sizeof ((tsgate*)0)->pes];
    static tsgate gate;
    tsgate_init(&gate);
    double t_tune = now(), t_stat = 0, t_unlock = 0, t_start = now();
    int locked = 0, ever = 0;
    unsigned long long bytes = 0, bytes_last = 0;
    for (;;) {
        struct pollfd pf = { .fd = dvr, .events = POLLIN };
        int r = poll(&pf, 1, 100);
        if (r > 0 && (pf.revents & POLLIN)) {
            ssize_t n = read(dvr, buf, sizeof buf);
            unsigned char* obuf = buf;
            if (n > 0) n -= n % 188;
            if (n > 0 && gate_on && !gate.open) {
                /* hold back everything but the tables until the first keyframe */
                n = (ssize_t)tsgate_filter(&gate, buf, (size_t)n, gbuf);
                obuf = gbuf;
                if (gate.open && !quiet)
                    fprintf(stderr, "t2rx: keyframe - stream open (%lu packets held back)\n", gate.dropped);
            }
            if (n > 0 && us >= 0) {
                for (ssize_t i = 0; i < n; ) {
                    size_t take = (size_t)(n - i) < sizeof pend - npend ? (size_t)(n - i) : sizeof pend - npend;
                    memcpy(pend + npend, obuf + i, take);
                    npend += take; i += take;
                    if (npend == sizeof pend) {
                        sendto(us, pend, npend, 0, (struct sockaddr*)&ua, sizeof ua);
                        npend = 0;
                    }
                }
                bytes += n;
            } else if (n > 0) {
                ssize_t w = 0;
                while (w < n) {
                    ssize_t k = write(1, obuf + w, n - w);
                    if (k < 0) { if (errno == EINTR) continue; return 0; }   /* player gone */
                    w += k;
                }
                bytes += n;
            } else if (n < 0 && errno == EOVERFLOW) {
                if (!quiet) fprintf(stderr, "t2rx: DVR buffer overflow\n");
            }
        }
        double t = now();
        if (t - t_stat >= 0.5) {
            fe_status_t st = 0;
            ioctl(fe, FE_READ_STATUS, &st);
            int lk = (st & FE_HAS_LOCK) != 0;
            double sig, cnr;
            stats(fe, &sig, &cnr);
            double rate = (bytes - bytes_last) * 8 / (t - (t_stat ? t_stat : t_start)) / 1e6;
            bytes_last = bytes;
            t_stat = t;
            if (lk && !ever) { ever = 1; if (!quiet) fprintf(stderr, "t2rx: LOCKED after %.1f s\n", t - t_tune); }
            if (lk) t_unlock = 0; else if (!t_unlock) t_unlock = t;
            locked = lk;
            char mod[8], fec[8], gi[8], fft[8];
            if (lk) l1(fe, mod, fec, gi, fft);
            else { strcpy(mod, "-"); strcpy(fec, "-"); strcpy(gi, "-"); strcpy(fft, "-"); }
            long per = per_delta(fe);
            if (statf) {
                char tmp[256];
                snprintf(tmp, sizeof tmp, "%s.tmp", statf);
                FILE* s = fopen(tmp, "w");
                if (s) {
                    fprintf(s, "state=%s sig=%.1f cnr=%.1f rate=%.2f mod=%s fec=%s gi=%s fft=%s per=%ld off=%d\n",
                            lk ? "LOCK" : (st & FE_HAS_CARRIER ? "SYNC" : "NOSIG"),
                            sig, cnr, rate, mod, fec, gi, fft, per, lk ? carrier_offset() : 0);
                    fclose(s);
                    rename(tmp, statf);          /* atomic for readers */
                }
            }
            if (!quiet)
                fprintf(stderr, "t2rx: %-5s signal %6.1f dBm  C/N %5.1f dB  TS %.2f Mb/s\n",
                        lk ? "LOCK" : (st & FE_HAS_CARRIER ? "SYNC" : "----"), sig, cnr, rate);
            if (ever && !lk && t_unlock && t - t_unlock > loss) {
                if (!quiet) fprintf(stderr, "t2rx: signal lost for %.0f s\n", loss);
                return 2;
            }
            if (!ever && t - t_tune > 10) {            /* no lock yet: re-tune */
                tune(fe, freq, bw_hz, plp);
                t_tune = t;
            }
        }
        (void)locked;
    }
}
