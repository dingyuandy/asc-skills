#!/usr/bin/env bash
set -euo pipefail

repo_root=${ASC_DEVKIT_ROOT:-}
work_dir=/mnt/e/asc_2201_ub_simd_microbenchmark
profile=smoke
case_regex='.*'
build_jobs=8
run_jobs=4
cmake_bin=${CMAKE_BIN:-}
python_bin=${PYTHON_BIN:-}
print_only=false

usage() {
    printf '%s\n' \
        'Usage: run_microbenchmark.sh [options]' \
        '  --repo PATH          asc-devkit repository root' \
        '  --work-dir PATH      intermediate output (default: /mnt/e/asc_2201_ub_simd_microbenchmark)' \
        '  --profile MODE       smoke or full (default: smoke)' \
        '  --case-regex REGEX   incremental selection; requires the other manifest logs to exist' \
        '  --build-jobs N       build parallelism (default: 8)' \
        '  --run-jobs N         simulator parallelism (default: 4)' \
        '  --cmake PATH         cmake executable; required when absent from PATH' \
        '  --python PATH        Python executable (default: python3 from PATH)' \
        '  --print-only         print resolved configuration without running' \
        '  -h, --help           show this help'
}

while (($#)); do
    case "$1" in
        --repo) repo_root=${2:?missing value}; shift 2 ;;
        --work-dir) work_dir=${2:?missing value}; shift 2 ;;
        --profile) profile=${2:?missing value}; shift 2 ;;
        --case-regex) case_regex=${2:?missing value}; shift 2 ;;
        --build-jobs) build_jobs=${2:?missing value}; shift 2 ;;
        --run-jobs) run_jobs=${2:?missing value}; shift 2 ;;
        --cmake) cmake_bin=${2:?missing value}; shift 2 ;;
        --python) python_bin=${2:?missing value}; shift 2 ;;
        --print-only) print_only=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
done

if [[ ${profile} != smoke && ${profile} != full ]]; then
    printf 'profile must be smoke or full\n' >&2
    exit 2
fi
if [[ ! ${build_jobs} =~ ^[1-9][0-9]*$ || ! ${run_jobs} =~ ^[1-9][0-9]*$ ]]; then
    printf 'build-jobs and run-jobs must be positive integers\n' >&2
    exit 2
fi
if [[ ${work_dir} != /mnt/e/* ]]; then
    printf 'work-dir must be an absolute child of /mnt/e: %s\n' "${work_dir}" >&2
    exit 2
fi

if [[ -z ${repo_root} ]]; then
    probe=${PWD}
    while [[ ${probe} != / ]]; do
        if [[ -f ${probe}/include/c_api/asc_simd.h ]]; then
            repo_root=${probe}
            break
        fi
        probe=${probe%/*}
        [[ -n ${probe} ]] || probe=/
    done
fi
if [[ -z ${repo_root} || ! -f ${repo_root}/include/c_api/asc_simd.h ]]; then
    printf 'cannot locate asc-devkit root; pass --repo or ASC_DEVKIT_ROOT\n' >&2
    exit 2
fi

bench_dir=${repo_root}/examples/02_simd_c_api/03_c_api/01_ub_vector_compute/simd_isa_microbenchmark
runner=${bench_dir}/run_all.sh
if [[ ! -x ${runner} ]]; then
    printf 'microbenchmark runner is missing or not executable: %s\n' "${runner}" >&2
    exit 2
fi

if [[ -z ${cmake_bin} ]]; then
    cmake_bin=$(command -v cmake || true)
fi
if [[ -z ${python_bin} ]]; then
    python_bin=$(command -v python3 || true)
fi
if [[ -z ${cmake_bin} || ! -x ${cmake_bin} ]]; then
    printf 'cannot locate cmake; pass --cmake PATH or CMAKE_BIN\n' >&2
    exit 2
fi
if [[ -z ${python_bin} || ! -x ${python_bin} ]]; then
    printf 'cannot locate Python; pass --python PATH or PYTHON_BIN\n' >&2
    exit 2
fi

printf 'repo=%s\nwork=%s\nprofile=%s\ncase_regex=%s\nbuild_jobs=%s\nrun_jobs=%s\ncmake=%s\npython=%s\n' \
    "${repo_root}" "${work_dir}" "${profile}" "${case_regex}" \
    "${build_jobs}" "${run_jobs}" "${cmake_bin}" "${python_bin}"

if [[ ${print_only} == true ]]; then
    exit 0
fi

env \
    WORK_DIR="${work_dir}" \
    BUILD_DIR="${work_dir}/build" \
    RUN_DIR="${work_dir}/runs" \
    PROFILE="${profile}" \
    CASE_REGEX="${case_regex}" \
    BUILD_JOBS="${build_jobs}" \
    RUN_JOBS="${run_jobs}" \
    CMAKE_BIN="${cmake_bin}" \
    PYTHON_BIN="${python_bin}" \
    "${runner}"
