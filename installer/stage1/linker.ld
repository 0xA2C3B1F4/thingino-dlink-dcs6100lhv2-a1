ENTRY(_start)

PHDRS
{
    text PT_LOAD FILEHDR PHDRS FLAGS(5);
    data PT_LOAD FLAGS(6);
}

SECTIONS
{
    . = 0x00400000 + SIZEOF_HEADERS;
    .text : { *(.text*) } :text
    .rodata : { *(.rodata*) } :text
    .MIPS.abiflags : { *(.MIPS.abiflags) } :text
    .reginfo : { *(.reginfo) } :text

    . = ALIGN(0x1000);
    .data : { *(.data*) *(.sdata*) } :data
    .bss (NOLOAD) : { *(.bss*) *(.sbss*) *(COMMON) } :data

    /DISCARD/ : {
        *(.comment)
        *(.pdr)
        *(.note*)
        *(.got*)
        *(.eh_frame*)
    }
}
