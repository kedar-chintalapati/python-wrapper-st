// Small shims for building LOST with MinGW on Windows. They change nothing on Linux or macOS.
// (Windows builds also need -D_USE_MATH_DEFINES for M_PI and friends.)

#ifndef PORTABILITY_H
#define PORTABILITY_H

#ifdef _WIN32
namespace lost {

/// POSIX rand_r, which Windows lacks. Only the dummy algorithms use it, and they just need some
/// reproducible sequence, so a simple linear congruential generator is enough.
inline int rand_r(unsigned int *seed) {
    *seed = *seed * 1103515245u + 12345u;
    return (int)((*seed / 65536u) % 32768u);
}

}  // namespace lost
#endif

#endif
