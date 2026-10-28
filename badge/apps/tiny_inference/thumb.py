"""Cortex-M33 DSP kernels; import only after checking the target."""

import micropython


@micropython.asm_thumb
def dot_i8(r0, r1, r2) -> int:
    mov(r3, 0)
    cmp(r2, 0)
    beq(DONE)
    label(LOOP)
    ldr(r4, [r0, 0])
    ldr(r5, [r1, 0])
    # SXTB16: sign-extend bytes 0 and 2 into two signed halfwords.
    data(2, 0xFA2F, 0xF684)
    data(2, 0xFA2F, 0xF785)
    # SMLAD r3, r6, r7, r3: two signed products per accumulation.
    data(2, 0xFB26, 0x3307)
    # Rotating by eight bits selects bytes 1 and 3.
    data(2, 0xFA2F, 0xF694)
    data(2, 0xFA2F, 0xF795)
    data(2, 0xFB26, 0x3307)
    add(r0, 4)
    add(r1, 4)
    sub(r2, 4)
    bne(LOOP)
    label(DONE)
    mov(r0, r3)


@micropython.asm_thumb
def requantize_i8(r0, r1) -> int:
    cmp(r1, 0)
    beq(SATURATE)
    mov(r2, 1)
    sub(r1, 1)
    lsl(r2, r1)
    add(r1, 1)
    cmp(r0, 0)
    blt(NEGATIVE)
    add(r0, r0, r2)
    asr(r0, r1)
    b(SATURATE)
    label(NEGATIVE)
    neg(r0, r0)
    add(r0, r0, r2)
    asr(r0, r1)
    neg(r0, r0)
    label(SATURATE)
    # SSAT r0, #8, r0.
    data(2, 0xF300, 0x0007)
