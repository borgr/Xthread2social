/* A stand-in for crypt32's CryptProtectData/CryptUnprotectData + kernel32's LocalFree.
 *
 * There is no Windows machine on this project, so the DPAPI path in config.py would
 * otherwise be the one piece of code that ships never having run. This exports the same
 * three symbols with the same DATA_BLOB layout and the same ownership rule (the caller
 * frees pDataOut.pbData), so the *marshalling* - struct layout, byref, the plaintext
 * buffer's lifetime, string_at, the LocalFree - is executed for real on any platform with
 * a C compiler. It is not encryption and is not meant to be: DPAPI's cryptography is
 * Microsoft's side of the contract, the ctypes plumbing is ours.
 */
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

typedef struct {
    uint32_t cbData;
    unsigned char *pbData;
} DATA_BLOB;

#define TAG "STUB"
#define TAGLEN 4
#define MASK 0x5A

int CryptProtectData(DATA_BLOB *in, const void *descr, const DATA_BLOB *entropy,
                     void *reserved, void *prompt, uint32_t flags, DATA_BLOB *out) {
    (void)descr; (void)entropy; (void)reserved; (void)prompt; (void)flags;
    if (!in || !in->pbData || !out) return 0;
    out->cbData = in->cbData + TAGLEN;
    out->pbData = (unsigned char *)malloc(out->cbData);
    if (!out->pbData) return 0;
    memcpy(out->pbData, TAG, TAGLEN);
    for (uint32_t i = 0; i < in->cbData; i++)
        out->pbData[TAGLEN + i] = in->pbData[i] ^ MASK;
    return 1;
}

int CryptUnprotectData(DATA_BLOB *in, void *descr, const DATA_BLOB *entropy,
                       void *reserved, void *prompt, uint32_t flags, DATA_BLOB *out) {
    (void)descr; (void)entropy; (void)reserved; (void)prompt; (void)flags;
    if (!in || !in->pbData || !out) return 0;
    /* Anything not written by the stub fails, the way a tampered or foreign blob does. */
    if (in->cbData < TAGLEN || memcmp(in->pbData, TAG, TAGLEN) != 0) return 0;
    out->cbData = in->cbData - TAGLEN;
    out->pbData = (unsigned char *)malloc(out->cbData ? out->cbData : 1);
    if (!out->pbData) return 0;
    for (uint32_t i = 0; i < out->cbData; i++)
        out->pbData[i] = in->pbData[TAGLEN + i] ^ MASK;
    return 1;
}

void *LocalFree(void *p) {
    free(p);
    return NULL;                 /* NULL on success, like the real one */
}
