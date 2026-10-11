package com.droiddeck.launcher.session

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class CompositorEnvTest {
    @Test fun noLineLeavesTheDefaultAlone() {
        assertNull(CompositorEnv.ubwc(emptyList()))
        assertNull(CompositorEnv.ubwc(listOf("TU_DEBUG=noubwc", "# DROIDDECK_WAYLAND_UBWC=0")))
    }

    @Test fun zeroTurnsCompressedBuffersOffAndOneOn() {
        assertEquals(false, CompositorEnv.ubwc(listOf("DROIDDECK_WAYLAND_UBWC=0")))
        assertEquals(true, CompositorEnv.ubwc(listOf("DROIDDECK_WAYLAND_UBWC=1")))
    }

    @Test fun theLastValidLineWinsAndSpacesAreIgnored() {
        assertEquals(true, CompositorEnv.ubwc(listOf("DROIDDECK_WAYLAND_UBWC=0", "  DROIDDECK_WAYLAND_UBWC=1  ")))
        assertEquals(false, CompositorEnv.ubwc(listOf("DROIDDECK_WAYLAND_UBWC=1", "DROIDDECK_WAYLAND_UBWC=0 ")))
    }

    @Test fun anythingButZeroOrOneIsIgnored() {
        assertEquals(false, CompositorEnv.ubwc(listOf("DROIDDECK_WAYLAND_UBWC=0", "DROIDDECK_WAYLAND_UBWC=maybe", "DROIDDECK_WAYLAND_UBWC=")))
        assertNull(CompositorEnv.ubwc(listOf("DROIDDECK_WAYLAND_UBWC=2")))
        assertNull(CompositorEnv.ubwc(listOf("XDROIDDECK_WAYLAND_UBWC=0")))
    }
}
