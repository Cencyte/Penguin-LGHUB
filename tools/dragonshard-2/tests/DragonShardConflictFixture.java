// Deliberately incompatible worker mutations to verify the three-way merge refuses data loss.
// @category DragonShard.Tests
import ghidra.app.script.GhidraScript;
import ghidra.framework.data.OpenMode;
import ghidra.framework.store.db.PackedDatabase;
import ghidra.program.database.ProgramDB;
import ghidra.program.model.listing.*;
import ghidra.program.model.address.*;
import ghidra.program.model.data.*;
import com.google.gson.*;
import java.io.*;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.HexFormat;

public class DragonShardConflictFixture extends GhidraScript {
    public void run() throws Exception {
        String[] args = getScriptArgs(); Path baseline = Paths.get(args[0]), root = Paths.get(args[1]);
        String hash = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(baseline)));
        for (String variant : new String[]{"left", "right"}) {
            Path folder = root.resolve(variant); Files.createDirectories(folder);
            PackedDatabase packed = PackedDatabase.getPackedDatabase(baseline.toFile(), true, monitor);
            Program program = new ProgramDB(packed.open(monitor), OpenMode.UPDATE, monitor, this);
            try {
                Address address = program.getFunctionManager().getFunctions(true).next().getEntryPoint();
                boolean widths = args.length > 2 && (args[2].equals("widths") || args[2].equals("types"));
                boolean types = args.length > 2 && args[2].equals("types");
                int extent = 1;
                if (widths) {
                    address = null;
                    for (AddressRange range : program.getListing().getUndefinedRanges(program.getMemory().getAllInitializedAddressSet(), false, monitor).getAddressRanges(true)) {
                        if (range.getLength() >= 8) { address = range.getMinAddress(); break; }
                    }
                    if (address == null) throw new IOException("Fixture has no undefined 8-byte span");
                    extent = types ? 4 : variant.equals("left") ? 1 : 8;
                }
                int transaction = program.startTransaction("Intentional conflicting fixture mutations");
                byte[] reviewed = new byte[8];
                if (program.getMemory().getBytes(address, reviewed) != reviewed.length)
                    throw new IOException("Fixture review span is unreadable");
                JsonObject policy = new JsonObject();
                policy.addProperty("schema", 1); policy.addProperty("sha256", program.getExecutableSHA256());
                JsonObject span = new JsonObject(); span.addProperty("address", address.toString());
                span.addProperty("hex", HexFormat.of().formatHex(reviewed));
                span.addProperty("reason", "Fixture-only reviewed width region; concrete differences must still be rejected");
                JsonArray spans = new JsonArray(); spans.add(span); policy.add("reviewed_untyped_widths", spans);
                Files.writeString(root.resolve("width-policy.json"), new Gson().toJson(policy));
                try {
                    if (types) program.getListing().createData(address, variant.equals("left") ? IntegerDataType.dataType : FloatDataType.dataType);
                    else if (widths) program.getListing().createData(address, Undefined.getUndefinedDataType(extent));
                    else program.getListing().setComment(address, CodeUnit.EOL_COMMENT, variant);
                } finally { program.endTransaction(transaction, true); }
                program.saveToPackedFile(folder.resolve("program.gzf").toFile(), monitor);
                JsonObject report = new JsonObject(); report.addProperty("complete", true);
                report.addProperty("sha256", program.getExecutableSHA256()); report.addProperty("baseline_sha256", hash);
                JsonArray range = new JsonArray(); range.add(address.toString()); range.add(address.add(extent - 1).toString());
                JsonArray ranges = new JsonArray(); ranges.add(range); report.add("changed_ranges", ranges);
                Files.writeString(folder.resolve("report.json"), new Gson().toJson(report));
            } finally { program.release(this); }
        }
    }
}
