// Copy saved analysis through Ghidra's read-only project API into this fresh project.
// @category DragonShard
import ghidra.app.script.GhidraScript;
import ghidra.framework.data.DefaultProjectData;
import ghidra.framework.model.*;
import ghidra.program.model.listing.Program;
import com.google.gson.*;
import java.nio.file.*;
import java.io.*;
import java.time.Instant;

public class DragonShardStage extends GhidraScript {
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length != 2) throw new IllegalArgumentException("source-manifest.json staging-report.json");
        JsonObject manifest;
        try (Reader r = Files.newBufferedReader(Paths.get(args[0]))) { manifest = JsonParser.parseReader(r).getAsJsonObject(); }
        Project destination = state.getProject();
        if (destination == null) throw new IllegalStateException("No fresh destination project");
        DomainFolder root = destination.getProjectData().getRootFolder();
        if (root.getFiles().length != 0 || root.getFolders().length != 0) throw new IllegalStateException("Destination must be empty");
        JsonArray copied = new JsonArray();
        for (JsonElement element : manifest.getAsJsonArray("programs")) {
                monitor.checkCancelled();
                JsonObject expected = element.getAsJsonObject();
                String sourceDir = expected.has("source_project_dir") ? expected.get("source_project_dir").getAsString() : manifest.get("project_dir").getAsString();
                String sourceName = expected.has("source_project_name") ? expected.get("source_project_name").getAsString() : manifest.get("project_name").getAsString();
                // Read saved program databases; never break a project lock or copy its live raw files.
                DefaultProjectData source = new DefaultProjectData(new ProjectLocator(sourceDir,sourceName),false,false);
                try {
                String path = expected.get("project_path").getAsString();
                DomainFile file = source.getFile(path);
                if (file == null) throw new IOException("Missing source program: " + path);
                long before = file.getLastModifiedTime();
                DomainFolder folder = root;
                String[] parts = path.substring(1).split("/");
                for (int i=0; i<parts.length-1; i++) {
                    DomainFolder child=folder.getFolder(parts[i]);
                    folder=child == null ? folder.createFolder(parts[i]) : child;
                }
                DomainFile copy = file.copyTo(folder,monitor);
                if (before != file.getLastModifiedTime()) throw new IOException("Source changed during copy: " + path);
                DomainObject object = copy.getReadOnlyDomainObject(this,DomainFile.DEFAULT_VERSION,monitor);
                try {
                    if (!(object instanceof Program)) throw new IOException("Not a program: " + path);
                    Program p=(Program)object;
                    String hash=p.getExecutableSHA256();
                    if (!expected.get("sha256").getAsString().equalsIgnoreCase(hash)) throw new IOException("SHA mismatch: " + path);
                    JsonObject record=new JsonObject();
                    record.addProperty("project_path",path); record.addProperty("sha256",hash);
                    record.addProperty("functions",p.getFunctionManager().getFunctionCount());
                    record.addProperty("symbols",p.getSymbolTable().getNumSymbols());
                    record.addProperty("source_modified",before);
                    copied.add(record); println("STAGED " + path + " functions=" + p.getFunctionManager().getFunctionCount());
                } finally { object.release(this); }
                } finally { source.close(); }
        }
        JsonObject report=new JsonObject(); report.addProperty("complete",true);
        report.addProperty("created",Instant.now().toString()); report.addProperty("method","saved Ghidra DomainFile.copyTo from read-only project view");
        report.add("programs",copied);
        Path output=Paths.get(args[1]);
        if (Files.exists(output)) throw new IOException("Refusing report overwrite");
        Files.writeString(output,new GsonBuilder().setPrettyPrinting().create().toJson(report)+"\n",StandardOpenOption.CREATE_NEW);
    }
}
