// Reunite statically reviewed interior/chained-unwind fragments on a private clone.
// @category DragonShard
import com.google.gson.*;
import ghidra.app.cmd.function.CreateFunctionCmd;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;
import java.io.IOException;
import java.util.*;

public class DragonShardFragmentRecovery extends DragonShardExceptionEvidence {
    private FlowType defaultFlow(Instruction ins) {
        return ins.getPrototype().getFlowType(ins.getInstructionContext());
    }
    @Override
    protected JsonArray prepare(Function parent, JsonObject request) throws Exception {
        if (System.getProperty("dragonshard.research.clone") == null)
            throw new IOException("Fragment recovery requires an explicitly isolated research clone");
        AddressSet checked = new AddressSet();
        for (JsonElement item : request.getAsJsonArray("byte_checks")) {
            JsonObject spec = item.getAsJsonObject();
            Address address = toAddr(spec.get("address").getAsString());
            byte[] expected = HexFormat.of().parseHex(spec.get("hex").getAsString());
            if (expected.length == 0) throw new IOException("Empty byte check");
            byte[] actual = new byte[expected.length];
            if (currentProgram.getMemory().getBytes(address, actual) != actual.length ||
                !Arrays.equals(actual, expected)) throw new IOException("Reviewed bytes differ at " + address);
            checked.add(address, address.add(expected.length - 1));
        }
        ArrayList<Function> fragments = new ArrayList<>();
        AddressSet fragmentInstructions = new AddressSet();
        AddressSet merged = new AddressSet(parent.getBody());
        JsonArray evidence = new JsonArray();
        ArrayList<Reference> jumpReferences = new ArrayList<>();
        for (JsonElement item : request.getAsJsonArray("reviewed_fragments")) {
            JsonObject spec = item.getAsJsonObject();
            Address address = toAddr(spec.get("address").getAsString());
            Function fragment = getFunctionAt(address);
            if (fragment == null || fragment.equals(parent) || !checked.contains(fragment.getBody()))
                throw new IOException("Missing or unverified fragment: " + address);
            ReferenceIterator refs = currentProgram.getReferenceManager().getReferencesTo(address);
            JsonArray flowRepairs = new JsonArray();
            while (refs.hasNext()) {
                Reference ref = refs.next();
                if (!ref.getReferenceType().isCall()) continue;
                Instruction ins = getInstructionAt(ref.getFromAddress());
                // Auto-analysis may turn an interior jump into a tail call after
                // inventing a function entry. Require the original opcode and
                // direct destination to prove that it is really a jump.
                if (ins == null || !checked.contains(ins.getMinAddress(), ins.getMaxAddress()) ||
                    !defaultFlow(ins).isJump() || !Arrays.asList(ins.getDefaultFlows()).contains(address))
                    throw new IOException("Fragment has a genuine/unverified call reference from " + ref.getFromAddress());
                JsonObject repair = new JsonObject(); repair.addProperty("from", ref.getFromAddress().toString());
                repair.addProperty("assembly", ins.toString());
                repair.addProperty("previous_flow_override", ins.getFlowOverride().toString());
                repair.addProperty("previous_reference_type", ref.getReferenceType().toString());
                repair.addProperty("default_flow_type", defaultFlow(ins).toString());
                flowRepairs.add(repair); jumpReferences.add(ref);
            }
            for (Instruction ins : currentProgram.getListing().getInstructions(fragment.getBody(), true))
                fragmentInstructions.add(ins.getAddress());
            merged.add(fragment.getBody()); fragments.add(fragment);
            JsonObject record = spec.deepCopy(); record.addProperty("previous_name", fragment.getName(true));
            record.addProperty("previous_body_bytes", fragment.getBody().getNumAddresses());
            record.addProperty("recovered_in_parent", parent.getEntryPoint().toString());
            record.addProperty("changes_only_in_private_program", true); evidence.add(record);
            record.add("reviewed_jump_flow_repairs", flowRepairs);
        }
        if (fragments.isEmpty()) throw new IOException("No reviewed fragments");
        int tx = currentProgram.startTransaction("Reviewed function fragment reunion");
        boolean success = false;
        try {
            for (Reference ref : jumpReferences) {
                Instruction ins = getInstructionAt(ref.getFromAddress());
                ins.setFlowOverride(FlowOverride.NONE);
                currentProgram.getReferenceManager().delete(ref);
                currentProgram.getReferenceManager().addMemoryReference(ref.getFromAddress(), ref.getToAddress(),
                    defaultFlow(ins), SourceType.USER_DEFINED, ref.getOperandIndex());
            }
            for (Function fragment : fragments)
                if (!currentProgram.getFunctionManager().removeFunction(fragment.getEntryPoint()))
                    throw new IOException("Cannot remove isolated fragment function");
            parent.setBody(merged);
            CreateFunctionCmd.fixupFunctionBody(currentProgram, parent, monitor);
            if (!parent.getBody().contains(fragmentInstructions))
                throw new IOException("Parent control flow did not recover all fragment instructions");
            success = true;
        } finally { currentProgram.endTransaction(tx, success); }
        return evidence;
    }
}
