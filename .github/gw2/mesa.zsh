#!/bin/zsh
# gw2 (jdrly/gw2-on-mac#339): build KosmicKrisp as Mach9 0.4.0's bin/build-mesa.sh built the
# engine's, on GitHub's runner. Only the Vulkan driver is built, out of a tree configured with
# Mach9's options for the whole stack (Zink and EGL too), so every object the driver links is
# compiled as the engine's was. Nothing here runs on my Mac or anywhere but the runner.
#
#   mesa.zsh tools                  stage 1: mesa_clc, vtn_bindgen2 and kk_clc for the host,
#                                   against Homebrew's LLVM, from the commit $SRC has checked out
#   mesa.zsh setup                  stage 2's tree: the driver for macOS $MACOSX_DEPLOYMENT_TARGET,
#                                   with the tools of stage 1 and no optional library
#   mesa.zsh build <git sha1>       the driver, its ICD manifest and the bridge library, with
#                                   MESA_GIT_SHA1_OVERRIDE=<git sha1> for the version string
#   mesa.zsh clean-objects          deletes every object the driver links, so the next build
#                                   compiles each again
#   mesa.zsh record <dir>           what compare.py record reads: ninja -t inputs of the driver,
#                                   ninja -t deps, ninja -t compdb
#   mesa.zsh tool-inputs <changed>  fails when one of the files <changed> lists (one a line) is
#                                   an input of the stage 1 tools, which are built once
#   mesa.zsh test <out> [base]      builds $TESTS/native/sparse_linear.m with
#                                   mtl_device.m's compile flags and the build's libmtl_bridge.a,
#                                   and runs its cases. With base, the engine's commit's bridge:
#                                   the stand-in case has to fail on sparseBufferTier.
#   mesa.zsh stage <dist>           the driver with the engine's install name, signed ad hoc, its
#                                   ICD manifest with the engine's library_path, Mesa's licence
#
# The environment names SRC (the Mesa checkout), BUILD (the directory of both trees), TESTS
# (tests/gw2 of the workflow's commit), MACOSX_DEPLOYMENT_TARGET and MESA_PYTHON_REQUIREMENTS. As
# in Mach9's paths, SRC is <root>/src-mesa and BUILD <root>/build/mesa, so the source paths in the
# driver are the engine's.
setopt err_exit pipe_fail no_unset

: ${SRC:?} ${BUILD:?} ${TESTS:?} ${MACOSX_DEPLOYMENT_TARGET:?} ${MESA_PYTHON_REQUIREMENTS:?}
DEPS_PREFIX=/opt/homebrew
TOOLS_BUILD=$BUILD/tools
DRIVER_BUILD=$BUILD/driver
VENV=$BUILD/venv
TOOLS=(src/compiler/clc/mesa_clc src/compiler/spirv/vtn_bindgen2 src/kosmickrisp/clc/kk_clc)
DYLIB=src/kosmickrisp/vulkan/libvulkan_kosmickrisp.dylib
ICD=src/kosmickrisp/vulkan/kosmickrisp_mesa_icd.aarch64.json
BRIDGE=src/kosmickrisp/bridge/libmtl_bridge.a
# the engine's manifest names the driver where the engine has it
ICD_LIBRARY_PATH=../../../lib/external/libvulkan_kosmickrisp.dylib
GW2=${0:A:h}

# Mesa's build scripts' Python modules, at Mach9's pinned versions
venv() {
  if [[ ! -x $VENV/bin/python3 ]]; then
    python3 -m venv "$VENV"
    "$VENV/bin/python3" -m pip install --quiet --disable-pip-version-check ${=MESA_PYTHON_REQUIREMENTS}
  fi
}

tools() {
  mkdir -p "$BUILD"
  venv
  local llvm_config=$DEPS_PREFIX/opt/llvm/bin/llvm-config
  cat >"$BUILD/tools-native.ini" <<EOF
[binaries]
c = '/usr/bin/clang'
cpp = '/usr/bin/clang++'
objc = '/usr/bin/clang'
objcpp = '/usr/bin/clang++'
llvm-config = '$llvm_config'
EOF
  (
    unset MACOSX_DEPLOYMENT_TARGET
    export PKG_CONFIG_PATH=$DEPS_PREFIX/lib/pkgconfig
    export PATH=$VENV/bin:$PATH
    meson setup "$TOOLS_BUILD" "$SRC" --native-file "$BUILD/tools-native.ini" \
      --buildtype=release \
      -Dplatforms=macos -Dvulkan-drivers=kosmickrisp -Dgallium-drivers= \
      -Dopengl=false -Dzstd=disabled \
      -Dmesa-clc=enabled -Dprecomp-compiler=enabled >/dev/null ||
      { cat "$TOOLS_BUILD/meson-logs/meson-log.txt" | tail -50; exit 1; }
    ninja -C "$TOOLS_BUILD" $TOOLS
  )
}

setup() {
  venv
  local min=-mmacosx-version-min=$MACOSX_DEPLOYMENT_TARGET
  cat >"$BUILD/driver-native.ini" <<EOF
[binaries]
c = '/usr/bin/clang'
cpp = '/usr/bin/clang++'
objc = '/usr/bin/clang'
objcpp = '/usr/bin/clang++'
mesa_clc = '$TOOLS_BUILD/src/compiler/clc/mesa_clc'
vtn_bindgen2 = '$TOOLS_BUILD/src/compiler/spirv/vtn_bindgen2'
kk_clc = '$TOOLS_BUILD/src/kosmickrisp/clc/kk_clc'
bison = '$DEPS_PREFIX/opt/bison/bin/bison'
flex = '/usr/bin/flex'
cmake = 'false'

[built-in options]
c_args = ['$min']
cpp_args = ['$min']
objc_args = ['$min']
c_link_args = ['$min', '-framework', 'CoreGraphics']
cpp_link_args = ['$min', '-framework', 'CoreGraphics']
objc_link_args = ['$min', '-framework', 'CoreGraphics']
EOF
  (
    # as Mach9's stage 2: no pkg-config search, and only the Python environment and the system
    # on PATH, so no library or program Homebrew happens to have changes what is built
    export PKG_CONFIG_LIBDIR=
    export NINJA=${commands[ninja]}
    local meson=${commands[meson]}
    export PATH=$VENV/bin:/usr/bin:/bin:/usr/sbin:/sbin
    "$meson" setup "$DRIVER_BUILD" "$SRC" --native-file "$BUILD/driver-native.ini" \
      --prefix="$BUILD/driver-install" \
      --buildtype=release \
      -Dplatforms=macos -Dvulkan-drivers=kosmickrisp -Dgallium-drivers=zink \
      -Dopengl=true -Degl=enabled -Dgles1=disabled -Dgles2=disabled \
      -Dglx=disabled -Dgbm=disabled -Ddraw-use-llvm=false \
      -Dvulkan-loader-rpath=@loader_path \
      -Dmesa-clc=system -Dprecomp-compiler=system \
      -Dllvm=disabled -Dspirv-tools=disabled \
      -Dzstd=disabled -Ddisplay-info=disabled -Dexpat=disabled \
      -Dxmlconfig=disabled -Dvalgrind=disabled -Dlibunwind=disabled \
      -Dlmsensors=disabled -Dglvnd=disabled -Dgallium-va=disabled >/dev/null ||
      { tail -50 "$DRIVER_BUILD/meson-logs/meson-log.txt"; exit 1; }
  )
}

build() {
  local sha1=${1:?the git sha1 for the version string}
  local ninja=${commands[ninja]:?no ninja}
  (
    export PATH=$VENV/bin:/usr/bin:/bin:/usr/sbin:/sbin
    MESA_GIT_SHA1_OVERRIDE=$sha1 "$ninja" -C "$DRIVER_BUILD" $DYLIB $ICD $BRIDGE
  )
  grep -F "(git-${sha1[1,10]})" "$DRIVER_BUILD/src/git_sha1.h"
}

objects() {
  ninja -C "$DRIVER_BUILD" -t inputs $DYLIB | grep '\.o$'
}

clean_objects() {
  local n=0 o
  for o in ${(f)"$(objects)"}; do
    rm -f "$DRIVER_BUILD/$o"
    (( ++n ))
  done
  echo "deleted $n objects"
  (( n > 0 ))
}

record() {
  local out=${1:?}
  mkdir -p "$out"
  ninja -C "$DRIVER_BUILD" -t inputs $DYLIB >"$out/inputs.txt"
  ninja -C "$DRIVER_BUILD" -t deps >"$out/deps.txt"
  ninja -C "$DRIVER_BUILD" -t compdb >"$out/compdb.json"
  python3 -I "$GW2/compare.py" record "$out" "$SRC" "$DRIVER_BUILD"
}

tool_inputs() {
  local changed=${1:?} inputs hit
  inputs=$(ninja -C "$TOOLS_BUILD" -t inputs $TOOLS | sed -n 's|^\(\.\./\)*src-mesa/||p')
  hit=$(grep -Fxf "$changed" <<<"$inputs" || true)
  if [[ -n $hit ]]; then
    echo "::error::this commit changes inputs of the stage 1 tools, which are built from the engine's commit only: $hit"
    return 1
  fi
  echo "no input of the stage 1 tools is among the $(wc -l <"$changed" | tr -d ' ') changed files ($(wc -l <<<"$inputs" | tr -d ' ') inputs)"
}

test_native() {
  local out=${1:?} base=${2:-} flags exe c st want
  mkdir -p "$out"
  flags=(${(0)"$(python3 -I "$GW2/compare.py" flags "$DRIVER_BUILD" src/kosmickrisp/bridge/mtl_device.m)"})
  (( $#flags > 5 )) || { echo "::error::no compile flags of mtl_device.m"; return 1; }
  exe=$out/sparse_linear
  /usr/bin/clang "${flags[@]}" ${base:+-DGW2_BASE} -o "$exe" "$TESTS/native/sparse_linear.m" \
    "$DRIVER_BUILD/$BRIDGE" -framework Foundation -framework Metal -framework QuartzCore -framework CoreGraphics
  if [[ -n $base ]]; then
    # the engine's commit: the stand-in case has to fail, and on sparseBufferTier
    st=0
    "$exe" stand-in-plain >"$out/base.txt" 2>&1 || st=$?
    cat "$out/base.txt"
    if (( st != 1 )) || ! grep -q 'FAIL: the bridge sent the buffer .*sparseBufferTier' "$out/base.txt"; then
      echo "::error::against the engine's commit the stand-in case exited $st without failing on sparseBufferTier, so it doesn't test the bug"
      return 1
    fi
    echo "the engine's commit: the stand-in case fails on sparseBufferTier, as it has to"
    return 0
  fi
  local failed=()
  for c in stand-in-plain stand-in-sparse plain heap nocopy sparse; do
    st=0
    "$exe" $c >"$out/$c.txt" 2>&1 || st=$?
    cat "$out/$c.txt"
    # only a real buffer the runner's Metal can't make may skip, never the plain one
    want=0
    if (( st == 77 )) && [[ $c == (heap|nocopy|sparse) ]]; then
      want=77
    fi
    (( st == want )) || failed+=("$c (exit $st)")
  done
  if (( $#failed )); then
    echo "::error::native test failed: ${failed[*]}"
    return 1
  fi
}

stage() {
  local dist=${1:?}
  mkdir -p "$dist"
  cp "$DRIVER_BUILD/$DYLIB" "$dist/"
  chmod u+w "$dist/libvulkan_kosmickrisp.dylib"
  # what meson install does to the file Mach9 staged: it deletes the build's rpaths (KosmicKrisp
  # has no install rpath)
  local rpath
  for rpath in ${(f)"$(otool -l "$dist/libvulkan_kosmickrisp.dylib" | awk '$1 == "cmd" { r = ($2 == "LC_RPATH") } r && $1 == "path" { print $2 }')"}; do
    echo "deleting the build's rpath $rpath"
    install_name_tool -delete_rpath "$rpath" "$dist/libvulkan_kosmickrisp.dylib" 2>/dev/null
  done
  install_name_tool -id @loader_path/libvulkan_kosmickrisp.dylib "$dist/libvulkan_kosmickrisp.dylib" 2>/dev/null
  codesign --force --sign - "$dist/libvulkan_kosmickrisp.dylib"
  codesign --verify --verbose "$dist/libvulkan_kosmickrisp.dylib"
  cp "$DRIVER_BUILD/$ICD" "$dist/"
  plutil -replace ICD.library_path -string "$ICD_LIBRARY_PATH" "$dist/${ICD:t}"
  [[ $(plutil -extract ICD.library_path raw -o - "$dist/${ICD:t}") == "$ICD_LIBRARY_PATH" ]]
  cp "$SRC/docs/license.rst" "$dist/LICENSE.rst"
}

case ${1:-} in
  tools) tools ;;
  setup) setup ;;
  build) build "${2:-}" ;;
  clean-objects) clean_objects ;;
  record) record "${2:-}" ;;
  tool-inputs) tool_inputs "${2:-}" ;;
  test) test_native "${2:-}" "${3:-}" ;;
  stage) stage "${2:-}" ;;
  *) echo "usage: mesa.zsh tools | setup | build <sha1> | clean-objects | record <dir> | tool-inputs <file> | test <out> [base] | stage <dist>" >&2; exit 2 ;;
esac
