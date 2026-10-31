// SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
// SPDX-License-Identifier: GPL-2.0-or-later
#define _GNU_SOURCE
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>
#include <GLES2/gl2ext.h>
#include <drm_fourcc.h>
#include <fcntl.h>
#include <gbm.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include <xf86drm.h>

static void require(int condition, const char *message)
{
    if (!condition) {
        fprintf(stderr, "PREFLIGHT_FAIL: %s (EGL=0x%x, GL=0x%x)\n",
                message, eglGetError(), glGetError());
        exit(1);
    }
}

static int has_extension(const char *list, const char *name)
{
    if (!list) return 0;
    size_t n = strlen(name);
    for (const char *p = list; (p = strstr(p, name)); p += n) {
        if ((p == list || p[-1] == ' ') && (p[n] == ' ' || p[n] == '\0')) return 1;
    }
    return 0;
}

static GLuint framebuffer(EGLImageKHR image)
{
    PFNGLEGLIMAGETARGETTEXTURE2DOESPROC bind_image =
        (PFNGLEGLIMAGETARGETTEXTURE2DOESPROC)eglGetProcAddress("glEGLImageTargetTexture2DOES");
    require(bind_image != NULL, "missing texture import function");
    GLuint texture = 0, fbo = 0;
    glGenTextures(1, &texture);
    glBindTexture(GL_TEXTURE_2D, texture);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    bind_image(GL_TEXTURE_2D, image);
    glGenFramebuffers(1, &fbo);
    glBindFramebuffer(GL_FRAMEBUFFER, fbo);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, texture, 0);
    require(glGetError() == GL_NO_ERROR, "texture/FBO import error");
    require(glCheckFramebufferStatus(GL_FRAMEBUFFER) == GL_FRAMEBUFFER_COMPLETE,
            "incomplete imported framebuffer");
    return fbo;
}

static void check_pixel(int x, int r, int g, int b)
{
    unsigned char pixel[4] = {0};
    glReadPixels(x, 2, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, pixel);
    require(glGetError() == GL_NO_ERROR, "pixel read error");
    printf("pixel[%d,2]=%u,%u,%u,%u\n", x, pixel[0], pixel[1], pixel[2], pixel[3]);
    require(abs((int)pixel[0] - r) <= 1 && abs((int)pixel[1] - g) <= 1 &&
            abs((int)pixel[2] - b) <= 1 && pixel[3] == 255, "pixel mismatch");
}

int main(int argc, char **argv)
{
    if (argc != 2) {
        fprintf(stderr, "usage: render-probe /dev/dri/cardN\n");
        return 2;
    }
    setvbuf(stdout, NULL, _IONBF, 0);
    require(geteuid() != 0, "must run as unprivileged builder");
    require(strncmp(argv[1], "/dev/dri/card", 13) == 0 && argv[1][13] != '\0', "not a primary DRM node");
    for (const char *p = argv[1] + 13; *p; ++p) require(*p >= '0' && *p <= '9', "invalid node suffix");
    int fd = open(argv[1], O_RDWR | O_CLOEXEC);
    require(fd >= 0, "cannot open DRM node");
    struct stat st;
    require(fstat(fd, &st) == 0 && S_ISCHR(st.st_mode), "not a character device");
    drmVersionPtr version = drmGetVersion(fd);
    require(version && version->name && strcmp(version->name, "vgem") == 0, "driver is not vgem");
    printf("uid=%u node=%s driver=%s\n", (unsigned)geteuid(), argv[1], version->name);
    drmFreeVersion(version);
    struct gbm_device *gbm = gbm_create_device(fd);
    require(gbm != NULL, "GBM device creation failed");
    PFNEGLGETPLATFORMDISPLAYEXTPROC get_display =
        (PFNEGLGETPLATFORMDISPLAYEXTPROC)eglGetProcAddress("eglGetPlatformDisplayEXT");
    require(get_display != NULL, "missing EGL platform function");
    EGLDisplay display = get_display(EGL_PLATFORM_GBM_KHR, gbm, NULL);
    require(display != EGL_NO_DISPLAY && eglInitialize(display, NULL, NULL), "GBM EGL initialization failed");
    const char *extensions = eglQueryString(display, EGL_EXTENSIONS);
    require(has_extension(extensions, "EGL_KHR_surfaceless_context") &&
            has_extension(extensions, "EGL_KHR_no_config_context") &&
            has_extension(extensions, "EGL_EXT_image_dma_buf_import"), "missing EGL features");
    require(has_extension(extensions, "EGL_EXT_image_dma_buf_import_modifiers"), "missing format/modifier discovery");
    PFNEGLQUERYDMABUFFORMATSEXTPROC query_formats =
        (PFNEGLQUERYDMABUFFORMATSEXTPROC)eglGetProcAddress("eglQueryDmaBufFormatsEXT");
    PFNEGLQUERYDMABUFMODIFIERSEXTPROC query_modifiers =
        (PFNEGLQUERYDMABUFMODIFIERSEXTPROC)eglGetProcAddress("eglQueryDmaBufModifiersEXT");
    require(query_formats && query_modifiers, "missing format query functions");
    EGLint formats[256], format_count = 0;
    require(query_formats(display, 0, NULL, &format_count) && format_count > 0 &&
            format_count <= 256, "format discovery failed");
    require(query_formats(display, format_count, formats, &format_count), "format retrieval failed");
    int found_format = 0;
    for (int i = 0; i < format_count; i++) found_format |= (uint32_t)formats[i] == DRM_FORMAT_XRGB8888;
    require(found_format, "XRGB8888 not advertised");
    EGLuint64KHR discovered[256];
    EGLBoolean external_only[256];
    EGLint discovered_count = 0;
    uint64_t allowed[258];
    int allowed_count = 0;
    EGLBoolean queried = query_modifiers(display, DRM_FORMAT_XRGB8888, 0,
                                         NULL, NULL, &discovered_count);
    require(discovered_count >= 0 && discovered_count <= 256, "invalid modifier count");
    if (queried && discovered_count > 0) {
        require(query_modifiers(display, DRM_FORMAT_XRGB8888, discovered_count,
                                discovered, external_only, &discovered_count), "modifier retrieval failed");
        for (int i = 0; i < discovered_count; i++) {
            printf("advertised_modifier=0x%016" PRIx64 " external_only=%u\n",
                   (uint64_t)discovered[i], external_only[i]);
            if (!external_only[i]) allowed[allowed_count++] = discovered[i];
        }
        require(allowed_count > 0, "format only supports external textures");
        int have_implicit = 0;
        for (int i = 0; i < allowed_count; i++) have_implicit |= allowed[i] == DRM_FORMAT_MOD_INVALID;
        if (!have_implicit) allowed[allowed_count++] = DRM_FORMAT_MOD_INVALID;
    } else {
        allowed[allowed_count++] = DRM_FORMAT_MOD_INVALID;
        allowed[allowed_count++] = DRM_FORMAT_MOD_LINEAR;
    }
    /* Match the pinned allocator's modifier attempt, then SCANOUT|RENDERING fallback. */
    struct gbm_bo *bo = NULL;
    if (!(allowed_count == 1 && allowed[0] == DRM_FORMAT_MOD_INVALID)) {
        bo = gbm_bo_create_with_modifiers(gbm, 16, 16, DRM_FORMAT_XRGB8888, allowed, allowed_count);
    }
    uint64_t modifier;
    if (bo) {
        modifier = gbm_bo_get_modifier(bo);
        puts("allocation=explicit-modifiers");
    } else {
        uint32_t flags = GBM_BO_USE_SCANOUT | GBM_BO_USE_RENDERING;
        int have_implicit = 0;
        for (int i = 0; i < allowed_count; i++) have_implicit |= allowed[i] == DRM_FORMAT_MOD_INVALID;
        if (allowed_count == 1 && allowed[0] == DRM_FORMAT_MOD_LINEAR) flags |= GBM_BO_USE_LINEAR;
        else require(have_implicit, "no permitted allocator fallback");
        bo = gbm_bo_create(gbm, 16, 16, DRM_FORMAT_XRGB8888, flags);
        modifier = (flags & GBM_BO_USE_LINEAR) ? DRM_FORMAT_MOD_LINEAR : DRM_FORMAT_MOD_INVALID;
        printf("allocation=flags 0x%x\n", flags);
    }
    require(bo != NULL, "KWin-style GBM render buffer allocation failed");
    require(gbm_bo_get_plane_count(bo) == 1, "unexpected plane count");
    int dma_fd = gbm_bo_get_fd(bo);
    require(dma_fd >= 0, "dma-buf export failed");
    printf("fourcc=0x%08x modifier=0x%016" PRIx64 " stride=%u offset=%u\n",
           gbm_bo_get_format(bo), modifier, gbm_bo_get_stride(bo), gbm_bo_get_offset(bo, 0));
    require(eglBindAPI(EGL_OPENGL_ES_API), "cannot bind GLES");
    EGLint context_attrs[] = {EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE};
    EGLContext context = eglCreateContext(display, EGL_NO_CONFIG_KHR, EGL_NO_CONTEXT, context_attrs);
    require(context != EGL_NO_CONTEXT && eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, context),
            "cannot create/make current GLES context");
    const char *renderer = (const char *)glGetString(GL_RENDERER);
    printf("GL_RENDERER=%s\n", renderer ? renderer : "(null)");
    require(renderer && strstr(renderer, "llvmpipe"), "actual renderer is not llvmpipe");
    EGLint attrs[32] = {EGL_WIDTH, 16, EGL_HEIGHT, 16, EGL_LINUX_DRM_FOURCC_EXT, DRM_FORMAT_XRGB8888,
                       EGL_DMA_BUF_PLANE0_FD_EXT, dma_fd, EGL_DMA_BUF_PLANE0_OFFSET_EXT, (EGLint)gbm_bo_get_offset(bo, 0),
                       EGL_DMA_BUF_PLANE0_PITCH_EXT, (EGLint)gbm_bo_get_stride(bo)};
    int end = 12;
    if (modifier != DRM_FORMAT_MOD_INVALID) {
        require(has_extension(extensions, "EGL_EXT_image_dma_buf_import_modifiers"), "missing modifier import");
        attrs[end++] = EGL_DMA_BUF_PLANE0_MODIFIER_LO_EXT;
        attrs[end++] = (EGLint)(modifier & 0xffffffff);
        attrs[end++] = EGL_DMA_BUF_PLANE0_MODIFIER_HI_EXT;
        attrs[end++] = (EGLint)(modifier >> 32);
    }
    attrs[end] = EGL_NONE;
    PFNEGLCREATEIMAGEKHRPROC create_image = (PFNEGLCREATEIMAGEKHRPROC)eglGetProcAddress("eglCreateImageKHR");
    PFNEGLDESTROYIMAGEKHRPROC destroy_image = (PFNEGLDESTROYIMAGEKHRPROC)eglGetProcAddress("eglDestroyImageKHR");
    require(create_image && destroy_image, "missing image functions");
    EGLImageKHR image = create_image(display, EGL_NO_CONTEXT, EGL_LINUX_DMA_BUF_EXT, NULL, attrs);
    require(image != EGL_NO_IMAGE_KHR, "first dma-buf import failed");
    framebuffer(image);
    glViewport(0, 0, 16, 16);
    glClearColor(0.2f, 0.4f, 0.6f, 1.0f);
    glClear(GL_COLOR_BUFFER_BIT);
    glEnable(GL_SCISSOR_TEST);
    glScissor(0, 0, 8, 16);
    glClearColor(1, 0, 0, 1);
    glClear(GL_COLOR_BUFFER_BIT);
    glDisable(GL_SCISSOR_TEST);
    glFinish();
    check_pixel(2, 255, 0, 0);
    check_pixel(12, 51, 102, 153);
    EGLImageKHR second = create_image(display, EGL_NO_CONTEXT, EGL_LINUX_DMA_BUF_EXT, NULL, attrs);
    require(second != EGL_NO_IMAGE_KHR, "second dma-buf import failed");
    framebuffer(second);
    check_pixel(2, 255, 0, 0);
    check_pixel(12, 51, 102, 153);
    destroy_image(display, second);
    destroy_image(display, image);
    eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
    eglDestroyContext(display, context);
    eglTerminate(display);
    close(dma_fd);
    gbm_bo_destroy(bo);
    gbm_device_destroy(gbm);
    close(fd);
    puts("PREFLIGHT_PASS: vgem GBM dma-buf export/import, llvmpipe, EGL FBO and pixel readback");
    return 0;
}
