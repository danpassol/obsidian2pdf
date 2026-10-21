{
  description = "Exporta notas de Obsidian a PDF corporativo (pandoc + WeasyPrint)";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      forAllSystems = f: nixpkgs.lib.genAttrs [ "x86_64-linux" "aarch64-linux" ]
        (system: f nixpkgs.legacyPackages.${system});
      pythonFor = pkgs: pkgs.python3.withPackages (p: [ p.weasyprint p.pyyaml ]);
    in
    {
      packages = forAllSystems (pkgs: {
        default = pkgs.stdenvNoCC.mkDerivation {
          pname = "obsidian2pdf";
          version = "unstable";
          src = self;
          nativeBuildInputs = [ pkgs.makeWrapper ];
          # ASSETS es la carpeta del script: style.css y template.html van a su lado
          installPhase = ''
            install -Dm644 -t $out/share/obsidian2pdf obsidian2pdf.py style.css template.html
            makeWrapper ${pythonFor pkgs}/bin/python3 $out/bin/obsidian2pdf \
              --add-flags $out/share/obsidian2pdf/obsidian2pdf.py \
              --prefix PATH : ${pkgs.lib.makeBinPath [ pkgs.pandoc ]} \
              --set-default FONTCONFIG_FILE ${pkgs.makeFontsConf {
                fontDirectories = [ pkgs.liberation_ttf pkgs.dejavu_fonts ];
              }}
          '';
          meta = {
            description = "Exporta notas de Obsidian a PDF corporativo";
            license = pkgs.lib.licenses.mit;
            mainProgram = "obsidian2pdf";
          };
        };
      });

      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell { packages = [ (pythonFor pkgs) pkgs.pandoc ]; };
      });
    };
}
