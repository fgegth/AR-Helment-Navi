/*
 * DRM Framebuffer Display — Continuous mode
 * Reads 1280x720 XR24 frames from stdin and displays on HDMI-A-1
 * Each frame: 1280*720*4 bytes. Reads next frame immediately after display.
 * Exits when stdin closes.
 */
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <unistd.h>
#include <sys/mman.h>
#include <sys/ioctl.h>
#include <sys/select.h>
#include <drm/drm.h>
#include <drm/drm_mode.h>

#define WIDTH  1280
#define HEIGHT 720
#define FRAME_SIZE (WIDTH * HEIGHT * 4)

int main() {
    int fd = open("/dev/dri/card0", O_RDWR);
    if (fd < 0) { perror("open dri"); return 1; }

    // Create dumb buffer
    struct drm_mode_create_dumb create = {
        .width  = WIDTH,
        .height = HEIGHT,
        .bpp    = 32,
    };
    if (ioctl(fd, DRM_IOCTL_MODE_CREATE_DUMB, &create)) {
        perror("CREATE_DUMB"); return 1;
    }

    // Map
    struct drm_mode_map_dumb map = { .handle = create.handle };
    if (ioctl(fd, DRM_IOCTL_MODE_MAP_DUMB, &map)) {
        perror("MAP_DUMB"); return 1;
    }

    unsigned char *buf = mmap(0, create.size, PROT_WRITE,
                              MAP_SHARED, fd, map.offset);
    if (buf == MAP_FAILED) { perror("mmap"); return 1; }

    // Add framebuffer
    struct drm_mode_fb_cmd fb_cmd = {
        .width  = WIDTH, .height = HEIGHT,
        .pitch  = create.pitch, .bpp = 32, .depth = 24,
        .handle = create.handle,
    };
    if (ioctl(fd, DRM_IOCTL_MODE_ADDFB, &fb_cmd)) {
        perror("ADDFB"); return 1;
    }

    // Set mode
    struct drm_mode_modeinfo mode = {
        .clock = 74250, .hdisplay = 1280, .hsync_start = 1390,
        .hsync_end = 1430, .htotal = 1650, .hskew = 0,
        .vdisplay = 720, .vsync_start = 725, .vsync_end = 730,
        .vtotal = 750, .vscan = 0, .vrefresh = 60,
        .flags = 0x5, .type = 0x40,
    };
    strncpy(mode.name, "1280x720", sizeof(mode.name));

    uint32_t conn_id = 156;
    struct drm_mode_crtc crtc = {
        .set_connectors_ptr = (uint64_t)(uintptr_t)&conn_id,
        .count_connectors = 1, .crtc_id = 71, .fb_id = fb_cmd.fb_id,
        .x = 0, .y = 0, .gamma_size = 0, .mode_valid = 1, .mode = mode,
    };
    if (ioctl(fd, DRM_IOCTL_MODE_SETCRTC, &crtc)) {
        perror("SETCRTC"); return 1;
    }

    // Signal ready
    fprintf(stderr, "READY\n");
    fflush(stderr);

    // Continuous frame loop
    size_t frame_offset = 0;
    unsigned char frame_buf[FRAME_SIZE];

    while (1) {
        // Read frame size (4 bytes, little-endian)
        uint32_t frame_size;
        ssize_t n = read(0, &frame_size, 4);
        if (n < 4) break;  // stdin closed

        // Read frame data
        size_t total = frame_size;
        size_t off = 0;
        while (off < total) {
            ssize_t r = read(0, frame_buf + off, total - off);
            if (r <= 0) goto done;
            off += r;
        }

        // Copy to framebuffer
        size_t copy_size = frame_size < create.size ? frame_size : create.size;
        memcpy(buf, frame_buf, copy_size);

        // Signal frame displayed
        fprintf(stderr, "FRAME\n");
        fflush(stderr);
    }

done:
    munmap(buf, create.size);
    close(fd);
    return 0;
}
